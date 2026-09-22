# Accessors for cells.env. Source after setting ROOT.
CELLS_ENV="${CELLS_ENV:-$ROOT/cells.env}"

cell_rows() { grep -vE '^[[:space:]]*(#|$)' "$CELLS_ENV"; }
cells_all() { cell_rows | awk '{print tolower($1)}' | tr '\n' ' ' | sed 's/ $//'; }

# cell_field <cell> <column>
cell_field() {
  cell_rows | awk -v c="$1" -v n="$2" 'tolower($1)==c {print $n; exit}'
}
preset_of()    { cell_field "$1" 2; }
rate_of()      { cell_field "$1" 3; }
k6cpu_of()     { cell_field "$1" 4; }
units_of()     { cell_field "$1" 5; }
target_ms_of() { cell_field "$1" 6; }
spans_of()     { cell_field "$1" 7; }
jdbc_of()      { cell_field "$1" 8; }
redis_of()     { cell_field "$1" 9; }
http_of()      { cell_field "$1" 10; }

require_cell() {
  if [ -z "$(preset_of "$1")" ]; then
    echo "unknown cell '$1' (not in $CELLS_ENV)" >&2
    return 1
  fi
}
