# Resolve roles.<$p>.<$r>.<$t> to "model<TAB>effort", or produce no output (fail closed).
# \A and \z anchor the whole string: "^...$" would accept a trailing newline.
def safe: strings | select(test("\\A[A-Za-z0-9][A-Za-z0-9._-]*\\z"));
if .schema_version != 2 then error("unsupported schema") else . end
| (.roles[$p][$r][$t] // error("no entry")) as $e
| ($e.model | safe) as $m
| ($e.effort | safe) as $f
| (.efforts[$p] | arrays | index($f)) as $i
| select($i != null)
| "\($m)\t\($f)"
