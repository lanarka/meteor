#!/usr/bin/env python3
"""
Test of the frontend. For every examples/NN_name.meteor and lib/name.meteor:

  1. the AST made by frontend.py must be exactly the same tree as the shipped
     examples/NN_name.json / lib/name.json (strict: 2 and 2.0 are different),
  2. meteor.py run on the generated ASTs must produce exactly the shipped dist/NN_name.quartz.

    python3 test_frontend.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

import frontend

HERE = os.path.dirname(os.path.abspath(__file__))


def difference(a, b, path="$"):
    """Strict tree comparison. Returns a description of the first difference or None."""
    if type(a) != type(b):
        return "%s: %r != %r" % (path, a, b)
    if isinstance(a, dict):
        if set(a) != set(b):
            return "%s: keys %s != %s" % (path, sorted(a), sorted(b))
        for key in a:
            found = difference(a[key], b[key], path + "." + key)
            if found:
                return found
    elif isinstance(a, list):
        if len(a) != len(b):
            return "%s: length %d != %d" % (path, len(a), len(b))
        for i in range(len(a)):
            found = difference(a[i], b[i], "%s[%d]" % (path, i))
            if found:
                return found
    elif a != b:
        return "%s: %r != %r" % (path, a, b)
    return None


def plain(value):
    """Cond (a dict subclass) -> dict, so that the strict type check compares like with like."""
    if isinstance(value, dict):
        return dict((k, plain(v)) for k, v in value.items())
    if isinstance(value, list):
        return [plain(v) for v in value]
    return value


def compile_file(path, module):
    with open(path, encoding="utf-8") as f:
        text = f.read()
    name = os.path.splitext(os.path.basename(path))[0]
    return plain(frontend.compile_source(text, module, name))


def main():
    failures = 0
    work = tempfile.mkdtemp(prefix="meteor_test_")
    try:
        for sub in ("lib", "examples", "dist"):
            os.makedirs(os.path.join(work, sub))
        shutil.copytree(os.path.join(HERE, "inc"), os.path.join(work, "inc"))

        jobs = []
        for sub, module in (("lib", True), ("examples", False)):
            folder = os.path.join(HERE, sub)
            for fname in sorted(os.listdir(folder)):
                if fname.endswith(".meteor"):
                    jobs.append((sub, fname[:-len(".meteor")], module))

        for sub, name, module in jobs:
            src = os.path.join(HERE, sub, name + ".meteor")
            try:
                ast = compile_file(src, module)
            except frontend.FrontendError as exc:
                print("FAIL  %s/%s.meteor: %s (line %s)" % (sub, name, exc.message, exc.line))
                failures += 1
                continue
            with open(os.path.join(HERE, sub, name + ".json"), encoding="utf-8") as f:
                expected = json.load(f)
            found = difference(expected, ast)
            print("%s  AST  %s/%s.meteor%s" % ("FAIL" if found else "ok  ", sub, name,
                                              "  " + found if found else ""))
            failures += bool(found)
            # the examples import "../lib/x.obj": the loader detects the format from the content
            target = os.path.join(work, sub, name + (".obj" if sub == "lib" else ".json"))
            with open(target, "w", encoding="utf-8") as f:
                json.dump(ast, f)
            if sub == "lib":
                with open(os.path.join(work, sub, name + ".json"), "w", encoding="utf-8") as f:
                    json.dump(ast, f)

        backend = os.path.join(HERE, "meteor.py")
        for sub, name, module in jobs:
            if module:
                continue
            out = os.path.join(work, "dist", name + ".quartz")
            run = subprocess.run([sys.executable, backend, os.path.join(work, "examples", name + ".json"),
                                  "-o", out], cwd=work, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 universal_newlines=True)
            reference = os.path.join(HERE, "dist", name + ".quartz")
            if run.returncode != 0:
                status = "FAIL  backend: " + run.stderr.strip().splitlines()[-1]
            elif not os.path.exists(reference):
                status = "skip  (no dist/%s.quartz)" % name
            else:
                with open(out) as a, open(reference) as b:
                    same = a.read() == b.read()
                status = "ok    asm identical to dist/" if same else "FAIL  asm differs from dist/"
            print("%s  %s" % (status.split("  ")[0].ljust(4), "ASM  examples/%s %s" % (
                name, status.split("  ", 1)[1] if "  " in status else "")))
            failures += status.startswith("FAIL")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print("\n%s" % ("all tests passed" if not failures else "%d test(s) FAILED" % failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
