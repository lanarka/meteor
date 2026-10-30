#!/usr/bin/env python3
"""
bsontool - convert Meteor ASTs between JSON and BSON (uses the `bson` library: pip install pymongo).

    python3 bsontool.py json2bson prog.json                  # -> prog.bson
    python3 bsontool.py json2bson prog.json -o out.bson
    python3 bsontool.py bson2json prog.bson                  # -> prog.json (pretty printed)

Number mapping: JSON integers become BSON int32 (or int64 when they do not fit), JSON floats
become BSON doubles, so 2 and 2.0 stay different. Key order is preserved.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from backend import bson_loads, bson_dumps   # thin wrappers around the 'bson' library (in meteor.py)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Convert Meteor ASTs between JSON and BSON")
    ap.add_argument("command", choices=("json2bson", "bson2json"))
    ap.add_argument("input")
    ap.add_argument("-o", "--output", help="output file (default: input with the other extension, '-' = stdout for bson2json)")
    args = ap.parse_args(argv)
    try:
        if args.command == "json2bson":
            with open(args.input, "r", encoding="utf-8") as f:
                obj = json.load(f)
            data = bson_dumps(obj)
            out = args.output or os.path.splitext(args.input)[0] + ".bson"
            with open(out, "wb") as f:
                f.write(data)
        else:
            with open(args.input, "rb") as f:
                obj = bson_loads(f.read())
            text = json.dumps(obj, indent=2, ensure_ascii=False) + "\n"
            out = args.output or os.path.splitext(args.input)[0] + ".json"
            if out == "-":
                sys.stdout.write(text)
                return 0
            with open(out, "w", encoding="utf-8") as f:
                f.write(text)
    except (OSError, ValueError) as e:
        sys.stderr.write("bsontool: %s\n" % e)
        return 1
    sys.stderr.write("bsontool: %s\n" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
