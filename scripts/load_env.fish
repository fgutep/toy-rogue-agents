# Load .env (standard KEY=value dotenv format) into the current fish shell.
# fish can't `source` a bash-style .env directly, and nothing in agentlab
# auto-loads it, so this is the bridge. Run with:
#
#   source scripts/load_env.fish
#
# Variables are exported (-gx) into this shell session only; nothing is
# written back to .env or persisted beyond the session.

set -l envfile .env
if test (count $argv) -ge 1
    set envfile $argv[1]
end

if not test -f $envfile
    echo "load_env.fish: $envfile not found (copy .env.example to .env first)" >&2
    exit 1
end

for line in (string match -rv '^\s*(#|$)' < $envfile)
    set -l parts (string split -m1 '=' -- $line)
    if test (count $parts) -eq 2
        set -l key (string trim $parts[1])
        set -l value (string trim $parts[2])
        if test -n "$value"
            set -gx $key $value
        end
    end
end

echo "load_env.fish: loaded from $envfile"
