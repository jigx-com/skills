"""Read facts out of a Jigx solution folder on disk (the folder that holds index.jigx).

Deliberately no YAML dependency: .jigx files are read line-wise. Every extractor here is
conservative; it would rather return nothing than guess.
"""
import os
import pathlib
import re
import sys

import ddprofile as profile_mod


def resolve_source(arg, solution, prof):
    """--source; else the profile's `source`; else $JIGX_WORKSPACE/<solution>; else the
    current directory or a direct child of it that holds index.jigx."""
    if arg:
        return pathlib.Path(arg).expanduser()
    if prof.get("source"):
        return profile_mod.resolve(prof, prof["source"])
    ws = os.environ.get("JIGX_WORKSPACE")
    if ws and solution and (pathlib.Path(ws).expanduser() / solution).exists():
        return pathlib.Path(ws).expanduser() / solution
    here = pathlib.Path.cwd()
    found = [here] if (here / "index.jigx").exists() else [d for d in here.iterdir()
                                                            if (d / "index.jigx").exists()]
    if len(found) == 1:
        return found[0]
    sys.exit("ddcheck: cannot tell which solution folder to read. Pass --source <folder with "
             "index.jigx>, or set \"source\" in the profile.")


def _jigx_files(source, kind=None):
    root = pathlib.Path(source)
    pattern = f"{kind}/**/*.jigx" if kind else "**/*.jigx"
    return sorted(p for p in root.glob(pattern) if p.is_file())


def declared_tables(source):
    """Table names from databases/default.jigx (YAML set keys `? name` or mapping keys
    `name:`), only inside the top-level `tables:` block. None when the file is absent."""
    path = pathlib.Path(source) / "databases/default.jigx"
    if not path.exists():
        return None
    out, inside = [], False
    for line in path.read_text().splitlines():
        if not inside:
            inside = line.rstrip() == "tables:"
            continue
        if line.strip() and not line.startswith(" "):
            break
        s = line.strip()
        if s.startswith("?"):
            name = s.lstrip("?").strip()
        elif s.endswith(":") and not s.startswith("#"):
            name = s[:-1].strip()
        else:
            continue
        if name:
            out.append(name)
    return out


_ENTITY_RE = re.compile(r"""^\s*entity:\s*['"]?default/([A-Za-z0-9_\-]+)""", re.M)


def written_tables(source):
    """{table: first file:line} for every default/<table> an execute-entity(ies) action writes.
    A table the app writes holds rep or sync data: never compared, copied or seeded."""
    out = {}
    for f in _jigx_files(source):
        text = f.read_text(errors="replace")
        if "execute-entit" not in text:
            continue
        for m in _ENTITY_RE.finditer(text):
            line = text.count("\n", 0, m.start()) + 1
            out.setdefault(m.group(1), f"{f.relative_to(source)}:{line}")
    return out


class DataTables:
    """Decides whether a table is data (written by the app or reps) or config."""

    def __init__(self, source, prof):
        self.written = written_tables(source) if source and pathlib.Path(source).exists() else {}
        dt = prof.get("dataTables") or {}
        self.names = set(dt.get("names") or [])
        self.suffixes = tuple(dt.get("suffixes") or ())
        self.declared_config = set(prof.get("configTables") or []) | set(prof.get("requiredTables") or [])

    def why(self, table):
        """Why `table` counts as data, or None when it is config."""
        if table in self.declared_config:
            return None
        if table in self.names:
            return "listed in the profile's dataTables"
        if self.suffixes and table.endswith(self.suffixes):
            return "matches a dataTables suffix in the profile"
        if table in self.written:
            return f"the app writes it ({self.written[table]})"
        return None


def jig_paths(source):
    """{jig id: path}. A jig's id is its filename stem; a top-level `id:` is accepted too."""
    out = {}
    for f in _jigx_files(source, "jigs"):
        out[f.stem] = f
        m = re.search(r"^id:\s*['\"]?([A-Za-z0-9_\-]+)", f.read_text(errors="replace"), re.M)
        if m:
            out.setdefault(m.group(1), f)
    return out


def jig_inputs(path):
    """Keys of a jig's top-level `inputs:` block, or None when it declares none."""
    inside, keys = False, []
    for line in pathlib.Path(path).read_text(errors="replace").splitlines():
        if not inside:
            if re.match(r"^inputs:\s*$", line):
                inside = True
            continue
        if line.strip() and not line.startswith(" "):
            break
        m = re.match(r"^  ([A-Za-z][A-Za-z0-9_\-]*):", line)
        if m:
            keys.append(m.group(1))
    return keys if inside else None


_PARAM_KEY_RE = re.compile(r"@ctx\.(?:jig\.)?inputs\.parameters\.([A-Za-z0-9_]+)")


def jig_parameter_keys(path):
    """Keys of a `parameters` input the jig actually reads."""
    return set(_PARAM_KEY_RE.findall(pathlib.Path(path).read_text(errors="replace")))


_TABLE_RE = re.compile(r"default/([A-Za-z0-9_\-]+)")
_JX = r"json_extract\(\s*(?:\w+\.)?data\s*,\s*'\$\.([A-Za-z0-9_]+)'\s*\)"
_BOOL_RE = re.compile(_JX + r"\s*(?:==?|!=|<>|IS(?:\s+NOT)?)\s*(true|false)\b", re.I)
_NUM_RE = re.compile(_JX + r"\s*(?:==?|!=|<>|<=|>=|<|>)\s*-?\d+(?:\.\d+)?\b")
_FLAG_RE = re.compile(_JX)


_DS_START = re.compile(r"^(\s*)(?:-\s+)?type:\s*datasource\.", re.M)


def datasources(source):
    """[(path, tables it names, text)] for every datasource definition, both the files under
    datasources/ and the ones declared inline in jigs. Each definition runs from its
    `type: datasource.*` line to the next one or the first line indented less than it, so its fields are
    tied only to the tables that same definition names."""
    out = []
    for f in _jigx_files(source):
        text = f.read_text(errors="replace")
        starts = [m.start() for m in _DS_START.finditer(text)]
        if not starts:
            continue
        for i, start in enumerate(starts):
            end = starts[i + 1] if i + 1 < len(starts) else len(text)
            lines = text[start:end].splitlines(keepends=True)
            indent = len(lines[0]) - len(lines[0].lstrip(" -"))
            keep = [lines[0]]
            for line in lines[1:]:
                if line.strip() and len(line) - len(line.lstrip()) < indent:
                    break                       # dedented below this definition: it has ended
                keep.append(line)
            chunk = "".join(keep)
            out.append((f, set(_TABLE_RE.findall(chunk)), chunk))
    return out


def typed_fields(source):
    """{table: {field: ('boolean'|'number', 'file')}} from datasource SQL.

    `json_extract(data,'$.x') = true` only matches JSON true (or 1): a string "true" never
    matches, so the row silently drops out. A numeric comparison likewise never matches a string.
    Applied to every table the datasource names; a row is only checked when it has the field.
    """
    out = {}
    for f, tables, text in datasources(source):
        found = {m.group(1): "boolean" for m in _BOOL_RE.finditer(text)}
        for m in _NUM_RE.finditer(text):
            found.setdefault(m.group(1), "number")
        for t in tables:
            for field, kind in found.items():
                out.setdefault(t, {}).setdefault(field, (kind, f.name))
    return out


def config_flags(source, table):
    """Every `$.<flag>` the datasources that read default/<table> extract."""
    flags = set()
    for _, tables, text in datasources(source):
        if table in tables:
            flags.update(_FLAG_RE.findall(text))
    return flags
