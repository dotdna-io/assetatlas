# Unified Fields

A **unified field** merges the values of two or more existing asset fields — typically the same concept arriving from different sources — into a single field on the asset, using a merge function you choose. This tab under **Settings → Data → Unified Fields** lists your unified fields and is where you create, edit, and delete them.

## Creating a unified field

- Click **Add unified field**.
- Enter a **Name**.
- Add two or more **Source fields** with the searchable **Add field** picker, then order them with the up/down controls — the order is the merge order.
- Pick a **Function**:
  - **Coalesce — first non-empty** takes the first source that has a value, in your order.
  - **Union — collect all values** gathers every value from every source.
  - **Most recent / highest** and **Earliest / lowest** are offered only for date and number fields.
- Watch the **Preview** — a live sample showing each source's value and the merged **Result** per asset — then click **Save**.

## Key points

- **At least two source fields** are required, and they must share a compatible type. Mixing incompatible types (say a date with text) is rejected.
- **Multi-value fields only support Union.** If a source holds a list — for example any identity field such as Hostname or FQDN, which are stored as lists — Union is the only valid function, and the editor selects it for you.
- **You can't build on built-in or unified fields.** The source picker hides virtual and unified fields; a unified field is composed only from real source fields.
- **Names must be unique** (case-insensitive), and if a chosen source field is later removed from the registry the unified field flags it and won't save until you drop the missing source.
