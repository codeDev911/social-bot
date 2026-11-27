import re
import sys

path = sys.argv[1]
source = open(path, encoding="utf-8").read()
# Jinja has no JS parser: replace statements/expressions with harmless literals.
source = re.sub(r"\{%.*?%\}", "", source, flags=re.S)
source = re.sub(r"\{\{.*?\}\}", "null", source, flags=re.S)

blocks = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", source, flags=re.S)
print(f"// {path}: {len(blocks)} inline script block(s)")

with open("_tmp_inline.js", "w", encoding="utf-8") as handle:
    for index, block in enumerate(blocks):
        handle.write(f"\n// ---- block {index} ----\n")
        handle.write(block)