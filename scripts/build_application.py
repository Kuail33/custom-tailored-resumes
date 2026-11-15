#!/usr/bin/env python3
"""
build_application.py — Build the three deliverables for one application from ONE shared
content definition, so the ATS resume, the Professional resume and the Professional cover
letter cannot drift apart.

    python scripts/build_application.py application_<Company>.json [--questions-asked] [--preview <new_dir>]

Input: application_<Company>.json (worked synthetic example: tests/fixtures/application_AcmeFreight.json) and, beside it,
evidence_map_<Company>.json (worked synthetic example: tests/fixtures/evidence_map_AcmeFreight.json).

Order of work:
  1. Evidence map: must exist and be complete. Open questions stop the build until they have
     been put to the candidate (--questions-asked builds with them left unclaimed).
  2. QA gate on the content itself, before anything is rendered.
  3. Render the three files with the existing builders (their behaviour is unchanged).
  4. QA gate again on the rendered Professional files; ATS check with role fit on the ATS resume.
  5. With --preview, render every page to PNG in a NEW folder for inspection.

Identity (name, credentials, contact, education) comes from the definition's "identity"
object, else reference/identity.json, else the Professional builder's defaults if it has any.
Nothing about the candidate is written in this script.
"""

import datetime
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import docx  # noqa: E402
import build_professional  # noqa: E402
import evidence_map  # noqa: E402
import qa_check  # noqa: E402
from build_cover_letter_professional import build as build_cover_letter  # noqa: E402
from build_docx import build_resume  # noqa: E402

REQUIRED = ("company", "date", "headline", "profile", "core_expertise", "education_block",
            "tech_stacks", "experience", "keywords", "cover_letter")


def runs(text):
    """"plain **bold** plain" -> [(text, bold), ...] for the ATS builder."""
    return [(p, i % 2 == 1) for i, p in enumerate(re.split(r"\*\*", text)) if p]


def identity_for(app):
    ident = app.get("identity")
    if not ident and (ROOT / "reference" / "identity.json").exists():
        ident = json.loads((ROOT / "reference" / "identity.json").read_text(encoding="utf-8"))
    ident = ident or getattr(build_professional, "DEFAULTS", None)
    if not ident or not all(ident.get(k) for k in ("name", "contact", "education")):
        sys.exit('No candidate identity found. Add an "identity" object (name, contact, education, optional '
                 "credentials) to the application definition or to reference/identity.json.")
    return ident


def _strip_period(text):
    """House style: no full stop at the end of a bullet, context line, or summary line."""
    return re.sub(r"\.\s*$", "", text.strip())


def ats_parts(app, ident):
    """The ATS resume as (header_lines, blocks) for build_docx.build_resume.
    Layout (the candidate's house style, see reference/style-guide.md):
    name / contact / summary line, EDUCATION, TECH STACKS, EXPERIENCE, PROJECTS."""
    name = ident["name"]
    contact = " | ".join(ident["contact"])
    header = [[(name, True)], [(contact, False)]]
    header += [runs(_strip_period(p)) for p in app["profile"]]

    blocks = [("blank", None), ("heading", "EDUCATION")]
    blocks += [("text", runs(line)) for line in app["education_block"]]

    blocks += [("blank", None), ("heading", "TECH STACKS")]
    for label, items in app["tech_stacks"].items():
        blocks.append(("text", [(f"{label}: ", True), (items, False)]))
    blocks += [("text", k) for k in app.get("knowledge_lines", [])]

    blocks += [("blank", None), ("heading", "EXPERIENCE")]
    for job in app["experience"]:
        company_line = f"{job['company']} | {job['location']}" if job.get("location") else job["company"]
        blocks += [("blank", None), ("heading", company_line), ("text", f"{job['title']} | {job['dates']}")]
        if job.get("context"):
            blocks.append(("text", runs(_strip_period(job["context"]))))
        blocks += [("bullet", runs(_strip_period(b))) for b in job["bullets"]]

    if app.get("projects"):
        blocks += [("blank", None), ("heading", "PROJECTS")]
        for proj in app["projects"]:
            blocks += [("blank", None), ("heading", f"{proj['name']} | {proj.get('link', 'Github')}")]
            blocks += [("bullet", runs(_strip_period(b))) for b in proj["bullets"]]
    return header, blocks


def _plain(parts):
    return "".join(t for t, _ in parts) if isinstance(parts, list) else (parts or "")


def letter_content(app, ident):
    cl = app["cover_letter"]
    d = datetime.date.fromisoformat(app["date"])
    content = {
        "name": ident["name"], "credentials": ident.get("credentials", ""), "contact": ident["contact"],
        "education": ident["education"],
        "certifications": app.get("certifications", ident.get("certifications", [])),
        "date": cl.get("date", f"{d:%B} {d.day}, {d.year}"), "recipient": cl.get("recipient", []),
        "salutation": cl.get("salutation", "Dear Hiring Manager,"), "paragraphs": cl["paragraphs"],
        "closing": cl.get("closing", "Sincerely,"), "closing_name": ident["name"],
        "core_expertise": app["core_expertise"],
    }
    if app.get("colors"):
        content["colors"] = app["colors"]
    return content


def gate_or_exit(errors, notices, label):
    if errors:
        qa_check.report(errors, label)
        sys.exit(1)
    qa_check.report_notices(notices, label)


def main(argv):
    sys.stdout.reconfigure(line_buffering=True)  # keep our output in order with the tools we call
    flags = {a for a in argv if a.startswith("--")}
    preview = argv[argv.index("--preview") + 1] if "--preview" in argv else None
    paths = [a for a in argv if not a.startswith("--") and a != preview]
    if len(paths) != 1:
        sys.exit(__doc__)
    app_path = Path(paths[0])
    app = json.loads(app_path.read_text(encoding="utf-8"))
    missing = [k for k in REQUIRED if not app.get(k)]
    if missing:
        sys.exit(f"{app_path.name} is missing: {', '.join(missing)}")
    company, date = app["company"], app["date"]
    ident = identity_for(app)

    # 1. evidence map before any drafting is rendered
    map_path = app_path.parent / f"evidence_map_{company}.json"
    emap = evidence_map.require(map_path, app["keywords"], questions_asked="--questions-asked" in flags)
    print(evidence_map.render(emap) + "\n")
    qa_check.use_evidence_map(emap)

    # 2. gate the shared content before rendering
    header, blocks = ats_parts(app, ident)
    resume_text = [_plain(h) for h in header] + [_plain(c) for kind, c in blocks if kind != "blank"]
    notices = []
    gate_or_exit(qa_check.check_paragraphs(resume_text, notices=notices), notices, "resume content")
    lc = letter_content(app, ident)
    letter_text = [lc["date"], *lc["recipient"], lc["salutation"], *lc["paragraphs"], lc["closing"]]
    notices = []
    gate_or_exit(qa_check.check_cover_letter(letter_text, notices=notices), notices, "cover letter content")

    # 3. render all three from the one definition
    out = ROOT / "output"
    out.mkdir(exist_ok=True)
    kw_path = ROOT / f"keywords_{company}.json"
    kw_path.write_text(json.dumps(app["keywords"], indent=2, ensure_ascii=False), encoding="utf-8")
    ats = out / f"Resume_{company}_{date}.docx"
    build_resume(str(ats), header, blocks)

    pro_content = {k: lc[k] for k in ("name", "credentials", "contact", "education", "certifications")}
    pro_content.update({"headline": app["headline"], "tagline": app.get("tagline", ""), "profile": app["profile"],
                        "core_expertise": app["core_expertise"],
                        "experience": [
                            {**job, "bullets": ([_strip_period(job["context"])] if job.get("context") else [])
                             + [_strip_period(b) for b in job["bullets"]]}
                            for job in app["experience"]
                        ] + [
                            {"company": p["name"], "title": "Project", "dates": "",
                             "bullets": [_strip_period(b) for b in p["bullets"]]}
                            for p in app.get("projects", [])
                        ]})
    if app.get("colors"):
        pro_content["colors"] = app["colors"]
    (ROOT / f"professional_{company}.json").write_text(json.dumps(pro_content, indent=2, ensure_ascii=False), encoding="utf-8")
    pro = out / f"Professional_{company}_{date}.docx"
    pages = build_professional.build(pro_content, str(pro))
    letter = out / f"Professional_CoverLetter_{company}_{date}.docx"
    build_cover_letter(lc, str(letter))

    # 4. gate the rendered Professional files (some builders do not gate themselves), then score
    for path, check in ((pro, qa_check.check_paragraphs), (letter, qa_check.check_cover_letter)):
        errors = check(qa_check._doc_text(docx.Document(str(path))))
        if errors:
            qa_check.report(errors, str(path))
            path.unlink(missing_ok=True)
            sys.exit(1)
    print(f"Built: {ats.name}, {pro.name} ({pages} page(s)), {letter.name} — QA gate passed on all three\n")
    subprocess.run([sys.executable, str(HERE / "ats_check.py"), str(ats), str(kw_path), "--map", str(map_path)])

    # 5. render every page for inspection
    if preview:
        pdir = Path(preview)
        if pdir.exists() and any(pdir.iterdir()):
            sys.exit(f"--preview needs a new, empty folder; {pdir} already has files (existing previews are never overwritten)")
        for path in (ats, pro, letter):
            subprocess.run([sys.executable, str(HERE / "preview_docx.py"), str(path), str(pdir)], check=True)
    else:
        print("Not rendered for inspection. Rerun with --preview <new_dir>, or run scripts/preview_docx.py on each file.")
    print("\nLook at every page of all three files after the LAST content change. A rebuild invalidates earlier inspection.")
    print(f"\n{qa_check.LIMITS}")


if __name__ == "__main__":
    main(sys.argv[1:])
