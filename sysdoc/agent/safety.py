"""Classify diagnostic commands so the scan phase reads the PC without changing it.

This guards against a model *accidentally* modifying the system while it
investigates. Commands recognised as read-only may run automatically (when the
user allowed scanning); commands that look like changes are refused and must be
proposed in a fix plan the user approves; anything unrecognised is shown to the
user for an explicit decision. It is a safety net, not a sandbox.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import PureWindowsPath
from typing import Callable


class Verdict(str, Enum):
    READ_ONLY = "read_only"
    CHANGES = "changes"
    UNKNOWN = "unknown"
    SENSITIVE = "sensitive"


READ_ONLY, CHANGES, UNKNOWN, SENSITIVE = Verdict.READ_ONLY, Verdict.CHANGES, Verdict.UNKNOWN, Verdict.SENSITIVE


@dataclass(frozen=True)
class Classification:
    verdict: Verdict
    reason: str


# --- Private data -----------------------------------------------------------

_SENSITIVE_TEXT = [re.compile(pattern, re.IGNORECASE) for pattern in (
    r"[\\/]\.ssh\b",
    r"\bid_(rsa|dsa|ecdsa|ed25519)\b",
    r"\.(pem|pfx|p12|kdbx|ppk)\b",
    r"\bwallet\.dat\b",
    r"\b(login data|web data|local state)\b",
    r"[\\/]cookies(\.sqlite)?\b",
    r"\b(logins\.json|key[34]\.db|signons\.sqlite)\b",
    r"local storage[\\/]leveldb",
    r"[\\/]microsoft[\\/](credentials|protect|vault)\b",
    r"[\\/]config[\\/](sam|security)\b",
    r"\bntds\.dit\b",
    r"[\\/]\.sysdoc\b",
    r"[\\/]\.aws[\\/]credentials",
    r"[\\/]\.docker[\\/]config\.json",
    r"\.git-credentials",
    r"[\\/][._]netrc\b",
    r"[\\/]\.(pypirc|npmrc)\b",
    r"(^|[\s\\/'\"])\.env(\.[\w-]+)?(?=$|[\s'\"])",
    r"\bkey\s*=\s*clear\b",
    r"\bssfn\w*",
    r"\b(vaultcmd|mimikatz|sekurlsa|lsass)\b",
    r"convertfrom-securestring|protecteddata\]::unprotect|\bget-credential\b",
)]
_SENSITIVE_SUFFIXES = {".pem", ".pfx", ".p12", ".kdbx", ".key", ".ppk", ".jks", ".keystore"}


def is_sensitive_path(path: str) -> bool:
    """True for files that hold passwords, keys, tokens, or Sysdoc's own API keys."""
    normalized = "\\" + str(path).replace("/", "\\")
    if PureWindowsPath(normalized).suffix.lower() in _SENSITIVE_SUFFIXES:
        return True
    return any(pattern.search(normalized) for pattern in _SENSITIVE_TEXT)


# --- PowerShell -------------------------------------------------------------

_READ_VERBS = frozenset({
    "get", "test", "measure", "select", "where", "sort", "group", "compare", "convertto", "convertfrom",
    "resolve", "find", "search", "join", "split", "foreach", "push", "pop",
})
_CHANGE_VERBS = frozenset({
    "set", "new", "remove", "add", "clear", "stop", "start", "restart", "suspend", "resume", "enable", "disable",
    "install", "uninstall", "register", "unregister", "update", "reset", "repair", "rename", "move", "copy",
    "mount", "dismount", "export", "out", "tee", "lock", "unlock", "grant", "revoke", "block", "unblock",
    "protect", "unprotect", "publish", "unpublish", "save", "send", "connect", "disconnect", "initialize",
    "optimize", "sync", "expand", "compress", "approve", "deny", "checkpoint", "restore", "backup", "limit",
    "edit", "merge", "write", "import", "submit", "deploy", "complete", "switch", "undo", "redo", "format",
})
_UNKNOWN_VERBS = frozenset({
    "invoke", "enter", "exit", "debug", "show", "read", "trace", "wait", "watch", "receive", "request", "use",
    "assert", "confirm", "open", "close",
})
_CMDLET_READ = frozenset({
    "format-table", "format-list", "format-wide", "format-custom", "format-hex",
    "out-string", "out-null", "out-host", "out-default",
    "write-output", "write-host", "write-verbose", "write-warning", "write-information", "write-debug",
    "write-progress", "start-sleep", "set-location", "set-strictmode", "set-variable", "new-object",
    "new-timespan", "new-variable", "add-member", "clear-host", "import-module", "import-csv", "import-clixml",
    "import-powershelldatafile", "wait-process",
})
_CMDLET_CHANGES = frozenset({"invoke-cimmethod", "invoke-wmimethod", "invoke-item", "get-windowsupdatelog"})
_CMDLET_UNKNOWN = frozenset({"get-clipboard"})

_PS_ALIASES = {
    "?": "where-object", "%": "foreach-object", "ac": "add-content", "cat": "get-content", "cd": "set-location",
    "chdir": "set-location", "clc": "clear-content", "clear": "clear-host", "cli": "clear-item",
    "clp": "clear-itemproperty", "cls": "clear-host", "clv": "clear-variable", "compare": "compare-object",
    "copy": "copy-item", "cp": "copy-item", "cpi": "copy-item", "cpp": "copy-itemproperty",
    "curl": "invoke-webrequest", "del": "remove-item", "diff": "compare-object", "dir": "get-childitem",
    "echo": "write-output", "epal": "export-alias", "epcsv": "export-csv", "erase": "remove-item",
    "fc": "format-custom", "fl": "format-list", "foreach": "foreach-object", "ft": "format-table",
    "fw": "format-wide", "gal": "get-alias", "gc": "get-content", "gcb": "get-clipboard", "gci": "get-childitem",
    "gcim": "get-ciminstance", "gcls": "get-cimclass", "gcm": "get-command", "gdr": "get-psdrive",
    "ghy": "get-history", "gi": "get-item", "gin": "get-computerinfo", "gjb": "get-job", "gl": "get-location",
    "gm": "get-member", "gmo": "get-module", "gp": "get-itemproperty", "gps": "get-process",
    "gpv": "get-itempropertyvalue", "group": "group-object", "gsv": "get-service", "gtz": "get-timezone",
    "gu": "get-unique", "gv": "get-variable", "gwmi": "get-wmiobject", "h": "get-history", "help": "get-help",
    "history": "get-history", "icim": "invoke-cimmethod", "icm": "invoke-command", "iex": "invoke-expression",
    "ihy": "invoke-history", "ii": "invoke-item", "ipal": "import-alias", "ipcsv": "import-csv",
    "ipmo": "import-module", "irm": "invoke-restmethod", "iwmi": "invoke-wmimethod", "iwr": "invoke-webrequest",
    "kill": "stop-process", "ls": "get-childitem", "man": "get-help", "md": "new-item", "measure": "measure-object",
    "mi": "move-item", "mkdir": "new-item", "mount": "new-psdrive", "move": "move-item", "mp": "move-itemproperty",
    "mv": "move-item", "nal": "new-alias", "ncim": "new-ciminstance", "ndr": "new-psdrive", "ni": "new-item",
    "nv": "new-variable", "ogv": "out-gridview", "popd": "pop-location", "ps": "get-process",
    "pushd": "push-location", "pwd": "get-location", "r": "invoke-history", "rcim": "remove-ciminstance",
    "rd": "remove-item", "rdr": "remove-psdrive", "ren": "rename-item", "ri": "remove-item", "rm": "remove-item",
    "rmdir": "remove-item", "rni": "rename-item", "rnp": "rename-itemproperty", "rp": "remove-itemproperty",
    "rv": "remove-variable", "rwmi": "remove-wmiobject", "sajb": "start-job", "sal": "set-alias",
    "saps": "start-process", "sasv": "start-service", "sc": "set-content", "scb": "set-clipboard",
    "scim": "set-ciminstance", "select": "select-object", "set": "set-variable", "si": "set-item",
    "sl": "set-location", "sleep": "start-sleep", "sls": "select-string", "sort": "sort-object",
    "sp": "set-itemproperty", "spjb": "stop-job", "spps": "stop-process", "spsv": "stop-service",
    "start": "start-process", "sv": "set-variable", "swmi": "set-wmiinstance", "tee": "tee-object",
    "type": "get-content", "wget": "invoke-webrequest", "where": "where-object", "wjb": "wait-job",
    "write": "write-output",
}
_PS_KEYWORDS = frozenset({
    "if", "elseif", "else", "foreach", "for", "while", "do", "until", "switch", "try", "catch", "finally",
    "return", "break", "continue", "throw", "trap", "exit", "begin", "process", "end", "param", "default",
})

_CHANGING_METHOD = re.compile(
    r"(?:\.|::)\s*(kill|delete\w*|remove\w*|move\w*|copy\w*|write(?:all\w*|text|bytes|lines)|create\w*|set\w*|"
    r"encrypt|decrypt|start|stop|appendall\w*|rename|shutdown|restart|reboot|uninstall|install|terminate|put|"
    r"commit|change\w*|enable\w*|disable\w*|reset\w*|update\w*|openwrite|replacefile)\s*\(",
    re.IGNORECASE,
)
_OPAQUE_METHOD = re.compile(
    r"(?:\.|::)\s*(invoke\w*|download\w*|upload\w*|send\w*|save\w*|import\w*|export\w*|execute\w*|run\w*|load\w*)\s*\(",
    re.IGNORECASE,
)
_OPAQUE_CALL = re.compile(r"&\s*[$(]|(?:^|[\s;|{(])\.\s+\S")
_CALL_QUOTED = re.compile(r"&\s*(['\"])([^'\"$]+)\1")
_ENCODED = re.compile(r"(?:^|\s)-e(?:c|nc|ncodedcommand)?\s+[A-Za-z0-9+/=]{20,}|frombase64string", re.IGNORECASE)
_REDIRECT = re.compile(r"(?:\d|\*)?>>?\s*(&\s*\d|\$null\b|nul\b)?", re.IGNORECASE)
_ASSIGNMENT = re.compile(r"^\$?[\w:.\[\]-]+\s*[+\-*/%]?=(?!=)\s*(.*)$", re.DOTALL)
_PS_SEPARATORS = re.compile(r"&&|\|\||[;|&\n\r{}()]")
_CMD_SEPARATORS = re.compile(r"&&|\|\||[&|\n\r()]")

# --- Native programs and cmd ------------------------------------------------

_NATIVE_READ = frozenset({
    "systeminfo", "tasklist", "whoami", "hostname", "driverquery", "nslookup", "ping", "tracert", "pathping",
    "getmac", "netstat", "quser", "qwinsta", "where", "findstr", "tree", "ver", "vol",
})
_NATIVE_CHANGES = frozenset({
    "del", "erase", "rd", "rmdir", "md", "mkdir", "copy", "xcopy", "robocopy", "move", "ren", "rename", "mklink",
    "takeown", "format", "diskpart", "shutdown", "taskkill", "tskill", "logoff", "regsvr32", "msiexec", "setx",
    "bcdboot", "cleanmgr", "defrag", "cipher", "compact", "label", "regedit", "gpupdate", "wuauclt", "usoclient",
})
# Destructive programs flagged anywhere in a cmd line, e.g. after `if exist ... del`.
_CMD_DESTRUCTIVE = _NATIVE_CHANGES - {"label"}
_CMD_READ_BUILTINS = frozenset({
    "dir", "type", "echo", "echo.", "@echo", "set", "path", "cd", "chdir", "cls", "title", "pushd", "popd", "color",
    "sort", "find", "fc",
})
_NETSH_CHANGES = frozenset({
    "set", "add", "delete", "reset", "install", "uninstall", "import", "dump", "start", "stop", "flush", "connect",
    "disconnect", "renew",
})
_NVIDIA_SETTERS = frozenset({
    "-pm", "--persistence-mode", "-e", "--ecc-config", "-p", "--reset-ecc-errors", "-c", "--compute-mode", "-r",
    "--gpu-reset", "-vm", "--virt-mode", "-am", "--accounting-mode", "-caa", "--clear-accounted-apps", "-pl",
    "--power-limit", "-cc", "--cuda-clear-caches", "-mig", "--multi-instance-gpu", "-gtt", "--gpu-target-temp",
    "-f", "--filename", "--auto-boost-default", "--auto-boost-permission",
})


def _subcommand(read: set[str]) -> Callable[[list[str]], Verdict]:
    def rule(args: list[str]) -> Verdict:
        first = ("/" + args[0][1:]) if args and args[0].startswith("-") else (args[0] if args else "")
        return READ_ONLY if first in read else CHANGES
    return rule


def _dism(args: list[str]) -> Verdict:
    joined = " ".join(args)
    if re.search(r"/(restorehealth|startcomponentcleanup|resetbase|add-|remove-|enable-|disable-|set-|apply-|"
                 r"capture-|commit-|mount-|unmount-|import-|export-|revert|cleanup-mountpoints|spsuperseded)", joined):
        return CHANGES
    if re.search(r"/(get-|checkhealth|scanhealth|analyzecomponentstore)", joined):
        return READ_ONLY
    return UNKNOWN


def _winget(args: list[str]) -> Verdict:
    if not args or args[0] in {"list", "ls", "search", "find", "show", "view", "--version", "-v", "--info", "features"}:
        return READ_ONLY
    listing_flags = {"--include-unknown", "-u", "--include-pinned", "--accept-source-agreements", "--disable-interactivity"}
    if args[0] in {"upgrade", "update"} and all(arg in listing_flags for arg in args[1:]):
        return READ_ONLY
    return READ_ONLY if args[:2] == ["source", "list"] else CHANGES


_NATIVE_RULES: dict[str, Callable[[list[str]], Verdict]] = {
    "ipconfig": lambda a: READ_ONLY if all(x in {"/all", "-all", "/displaydns", "/allcompartments", "/?"} for x in a) else CHANGES,
    "arp": lambda a: READ_ONLY if not a or a[0] in {"-a", "/a", "-g", "/g"} else CHANGES,
    "route": lambda a: READ_ONLY if not a or a[0] == "print" else CHANGES,
    "netsh": lambda a: READ_ONLY if "show" in a and not set(a) & _NETSH_CHANGES else CHANGES,
    "sc": _subcommand({
        "query", "queryex", "qc", "qdescription", "qfailure", "qfailureflags", "qsidtype", "qprivs", "qtriggerinfo",
        "qpreferrednode", "qmanagedaccount", "qprotection", "quserservice", "enumdepend", "getdisplayname",
        "getkeyname", "sdshow", "showsid",
    }),
    "reg": _subcommand({"query", "compare"}),
    "wevtutil": _subcommand({
        "qe", "query-events", "el", "enum-logs", "gl", "get-log", "gli", "get-loginfo", "ep", "enum-publishers",
        "gp", "get-publisher",
    }),
    "schtasks": lambda a: READ_ONLY if not a or a[0] == "/query" else CHANGES,
    "powercfg": _subcommand({
        "/list", "/l", "/query", "/q", "/getactivescheme", "/availablesleepstates", "/a", "/devicequery",
        "/lastwake", "/waketimers", "/requests", "/aliases", "/qh", "/?",
    }),
    "sfc": lambda a: READ_ONLY if a and (a[0] == "/verifyonly" or a[0].startswith("/verifyfile")) else CHANGES,
    "chkdsk": lambda a: READ_ONLY if all(re.fullmatch(r"[a-z]:", x) for x in a) else CHANGES,
    "bcdedit": lambda a: READ_ONLY if not a or a[0] in {"/enum", "/v"} else CHANGES,
    "dism": _dism,
    "fsutil": lambda a: READ_ONLY if a and (a[0] == "fsinfo" or {"query", "diskfree"} & set(a[1:3])) else CHANGES,
    "wmic": lambda a: CHANGES if set(a) & {"call", "set", "delete", "create"} else (READ_ONLY if set(a) & {"get", "list"} else UNKNOWN),
    "winget": _winget,
    "gpresult": lambda a: CHANGES if set(a) & {"/h", "/x"} else READ_ONLY,
    "w32tm": lambda a: READ_ONLY if a and a[0] in {"/query", "/tz", "/stripchart"} else CHANGES,
    "pnputil": lambda a: READ_ONLY if a and a[0].lstrip("/-").startswith("enum-") else CHANGES,
    "manage-bde": lambda a: READ_ONLY if a and a[0] in {"-status", "/status"} else CHANGES,
    "nvidia-smi": lambda a: CHANGES if any(x.split("=", 1)[0] in _NVIDIA_SETTERS for x in a) else READ_ONLY,
    "cmdkey": lambda a: READ_ONLY if a and a[0].startswith("/list") else CHANGES,
    "vssadmin": lambda a: READ_ONLY if a and a[0] == "list" else CHANGES,
    "net": lambda a: READ_ONLY if len(a) == 1 and a[0] in {
        "start", "use", "user", "localgroup", "share", "view", "config", "statistics", "accounts", "session",
    } else CHANGES,
    "attrib": lambda a: CHANGES if any(x[:1] in "+-" for x in a) else READ_ONLY,
    "icacls": lambda a: CHANGES if any(x.startswith("/") and x not in {"/t", "/c", "/l", "/q"} for x in a) else READ_ONLY,
    "klist": lambda a: CHANGES if {"purge", "purge_bind"} & set(a) else READ_ONLY,
    "date": lambda a: READ_ONLY if a == ["/t"] else CHANGES,
    "time": lambda a: READ_ONLY if a == ["/t"] else CHANGES,
}


# --- Parsing ----------------------------------------------------------------

def _basename(word: str) -> str:
    parts = [part for part in re.split(r"[\\/]", word.strip("\"'")) if part]
    return parts[-1] if parts else word


def _subexpressions(body: str) -> str:
    """Keep only the $(...) code inside a double-quoted string."""
    parts = []
    start = body.find("$(")
    while start != -1:
        depth, end = 1, start + 2
        while end < len(body) and depth:
            depth += {"(": 1, ")": -1}.get(body[end], 0)
            end += 1
        parts.append("(" + _strip_powershell(body[start + 2:end - 1]) + ")")
        start = body.find("$(", end)
    return " ".join(parts) if parts else "''"


def _strip_powershell(text: str) -> str:
    """Blank out comments and string literals so their contents aren't mistaken for commands."""
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        char = text[i]
        if text.startswith("<#", i):
            end = text.find("#>", i + 2)
            i = n if end == -1 else end + 2
            out.append(" ")
        elif char == "#":
            end = text.find("\n", i)
            i = n if end == -1 else end
        elif text.startswith(("@'", '@"'), i) and text[i + 2:i + 3] in ("\n", "\r"):
            quote = text[i + 1]
            end = text.find("\n" + quote + "@", i + 2)
            body = text[i + 2:] if end == -1 else text[i + 2:end]
            out.append(" '' " if quote == "'" else f" {_subexpressions(body)} ")
            i = n if end == -1 else end + 3
        elif char == "'":
            end = i + 1
            while end < n and not (text[end] == "'" and text[end + 1:end + 2] != "'"):
                end += 2 if text[end] == "'" else 1
            out.append(" '' ")
            i = end + 1
        elif char == '"':
            end = i + 1
            while end < n and text[end] != '"':
                end += 2 if text[end] == "`" else 1
            out.append(f" {_subexpressions(text[i + 1:end])} ")
            i = end + 1
        elif char == "`":
            out.append(" ")
            i += 2
        else:
            out.append(char)
            i += 1
    return "".join(out)


def _classify_cmdlet(name: str, args: list[str]) -> Verdict | None:
    if "-" not in name:
        return None
    if name in _CMDLET_READ:
        return READ_ONLY
    if name in _CMDLET_CHANGES:
        return CHANGES
    if name in _CMDLET_UNKNOWN:
        return UNKNOWN
    verb = name.split("-", 1)[0]
    if verb in _READ_VERBS:
        return CHANGES if {"-repair", "-fix"} & set(args) else READ_ONLY
    if verb in _CHANGE_VERBS:
        return CHANGES
    if verb in _UNKNOWN_VERBS:
        return UNKNOWN
    return None


def _explain(verdict: Verdict, word: str) -> str:
    if verdict is CHANGES:
        return f"'{word}' changes files, settings, or running programs."
    if verdict is UNKNOWN:
        return f"Sysdoc can't confirm that '{word}' only reads information."
    return ""


def _classify_chunk(chunk: str, powershell: bool) -> tuple[Verdict, str]:
    words = chunk.split()
    lowered = [word.lower() for word in words]
    if powershell:
        while words and lowered[0] in _PS_KEYWORDS:
            words, lowered = words[1:], lowered[1:]
    else:
        for word in words:
            if _basename(word).lower().removesuffix(".exe") in _CMD_DESTRUCTIVE:
                return CHANGES, _explain(CHANGES, word)
        while words and lowered[0] in {"do", "else", "not"}:
            words, lowered = words[1:], lowered[1:]
    if not words:
        return READ_ONLY, ""
    first = words[0]
    if powershell and lowered[0] in {"function", "filter", "class", "enum"}:
        return READ_ONLY, ""
    if not powershell and lowered[0] in {"for", "rem", "::"}:
        return READ_ONLY, ""
    if not powershell and lowered[0] == "if":
        return UNKNOWN, "Sysdoc can't check conditional cmd commands."

    assignment = _ASSIGNMENT.match(" ".join(words))
    if assignment:
        return _classify_chunk(assignment.group(1), powershell)
    if powershell and first.startswith("$") and "in" in lowered:
        return _classify_chunk(" ".join(words[lowered.index("in") + 1:]), powershell)
    if powershell and first.startswith((".\\", "./", "..\\", "../")):
        return UNKNOWN, f"It runs the local script or program '{first}'."

    has_path = "\\" in first or "/" in first
    name = _basename(first).lower()
    args = lowered[1:]
    if powershell and not has_path:
        verdict = _classify_cmdlet(_PS_ALIASES.get(name, name), args)
        if verdict is not None:
            return verdict, _explain(verdict, first)
    if first[0] in "$@[.0123456789-+*/!,<>:'\"=%?":
        return READ_ONLY, ""

    program = re.sub(r"\.(exe|com)$", "", name)
    if (not powershell and program in _CMD_READ_BUILTINS) or program in _NATIVE_READ:
        return READ_ONLY, ""
    if program in _NATIVE_CHANGES:
        return CHANGES, _explain(CHANGES, first)
    rule = _NATIVE_RULES.get(program)
    if rule is not None:
        verdict = rule(args)
        return verdict, _explain(verdict, " ".join(words[:2]))
    return UNKNOWN, f"Sysdoc doesn't recognise '{first}'."


def classify_command(command: str, shell: str = "powershell") -> Classification:
    """Decide whether a scan-phase command may run, must be approved, or is refused."""
    text = command.strip()
    if not text:
        return Classification(UNKNOWN, "The command is empty.")
    if any(pattern.search(text) for pattern in _SENSITIVE_TEXT):
        return Classification(SENSITIVE, "It would access passwords, keys, tokens, or other private data.")
    if _ENCODED.search(text):
        return Classification(UNKNOWN, "It contains encoded content that can't be checked.")

    powershell = shell != "cmd"
    if powershell:
        text = _CALL_QUOTED.sub(lambda match: f" {_basename(match.group(2)).replace(' ', '_')} ", text)
        code = _strip_powershell(text)
    else:
        code = re.sub(r'"[^"]*"?', ' "" ', text).replace("'", " ")

    if any(match.group(1) is None for match in _REDIRECT.finditer(code)):
        return Classification(CHANGES, "It writes output to a file.")
    if powershell:
        if match := _CHANGING_METHOD.search(code):
            return Classification(CHANGES, f"It calls the .NET method {match.group(1)}(), which can change the system.")
        if match := _OPAQUE_METHOD.search(code):
            return Classification(UNKNOWN, f"Sysdoc can't check what the .NET method {match.group(1)}() does.")
        if _OPAQUE_CALL.search(code):
            return Classification(UNKNOWN, "It runs a script or command whose contents can't be checked.")

    unknown_reason = None
    for chunk in (_PS_SEPARATORS if powershell else _CMD_SEPARATORS).split(code):
        verdict, reason = _classify_chunk(chunk, powershell)
        if verdict is CHANGES:
            return Classification(CHANGES, reason)
        if verdict is UNKNOWN and unknown_reason is None:
            unknown_reason = reason
    if unknown_reason:
        return Classification(UNKNOWN, unknown_reason)
    return Classification(READ_ONLY, "It only reads information.")
