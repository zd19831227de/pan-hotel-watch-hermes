"""
Validates a generated .pdf for structural corruption that a naive "the
script didn't crash" check would miss — a PDF can have a broken xref table,
a dangling object reference, or zero pages while every prior step still
"succeeds" silently.

Usage:
    python verify_pdf.py <file.pdf>

Exits 0 and prints "OK" only if the file is clean. Exits 1 and lists every
problem otherwise. Always run this immediately after build_pdf.py.
"""
import sys


def main():
    path = sys.argv[1]
    problems = []

    try:
        import fitz  # pymupdf
    except ImportError:
        print("ERROR: pymupdf not installed (pip install pymupdf)", file=sys.stderr)
        sys.exit(2)

    try:
        doc = fitz.open(path)
    except Exception as e:
        print(f"FAIL — could not open {path}: {e}", file=sys.stderr)
        sys.exit(1)

    if doc.is_encrypted:
        problems.append("document is encrypted/password-protected")

    if doc.page_count == 0:
        problems.append("document has 0 pages")

    for i in range(doc.page_count):
        try:
            page = doc.load_page(i)
            page.get_text()
            page.get_images(full=True)
        except Exception as e:
            problems.append(f"page {i + 1}: failed to read content: {e}")

    try:
        bad_xrefs = doc.xref_length() - 1
        if bad_xrefs <= 0:
            problems.append("empty xref table")
    except Exception as e:
        problems.append(f"xref check failed: {e}")

    doc.close()

    if problems:
        print(f"FAIL — {len(problems)} problem(s) in {path}:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        sys.exit(1)

    print(f"OK — {path} passed structural validation.")


if __name__ == "__main__":
    main()
