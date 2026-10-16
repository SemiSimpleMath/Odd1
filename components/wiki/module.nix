# odd1-wiki (CLI), the wiki MCP server and tests. The wiki itself is wiki/ in the repository, read from the deployed
# release (/var/lib/odd1/deploy/current/wiki).
{ pkgs, ... }:

let
  python = pkgs.odd1Python.withPackages (p: [ p.pytest p.mcp ]);
  env = ''export PYTHONPATH=${./.}''${PYTHONPATH:+:$PYTHONPATH}'';
  wrap = name: module: pkgs.writeShellScriptBin name ''
    ${env}
    exec ${python}/bin/python3 -m ${module} "$@"
  '';
  cli = wrap "odd1-wiki" "odd1_wiki.cli";
  server = wrap "odd1-wiki-mcp" "odd1_wiki.mcp_server";
  test = pkgs.writeShellScriptBin "odd1-wiki-test" ''
    ${env}
    export PYTHONDONTWRITEBYTECODE=1
    exec ${python}/bin/python3 -m pytest -q ${./.}/tests -p no:cacheprovider "$@"
  '';
in {
  environment.systemPackages = [ cli server test ];
  # The agent entry point (Odd1 convention): wiki_search, wiki_page.
  odd1.mcpServers.wiki = { command = "${server}/bin/odd1-wiki-mcp"; args = [ ]; };
}
