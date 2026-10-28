{
  lib,
  root ? ../../.,
}:
lib.cleanSourceWith {
  name = "agent-workspace-python-source";
  src = root;
  filter =
    path: _type:
    let
      rel = lib.removePrefix (toString root + "/") (toString path);
      base = baseNameOf (toString path);
    in
    base != "__pycache__"
    && !(lib.hasSuffix ".pyc" base)
    && (
      toString path == toString root
      || rel == "scripts"
      || rel == "scripts/__init__.py"
      || rel == "scripts/agent_workspaces"
      || lib.hasPrefix "scripts/agent_workspaces/" rel
    );
}
