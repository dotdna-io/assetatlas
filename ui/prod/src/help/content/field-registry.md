# Field Registry

The field registry is the catalog of every asset field AssetAtlas knows about, with a human display name, a description, and synonyms. Those descriptions and synonyms drive semantic search in the Explorer, so editing them refines match quality. It's the **Semantic** tab under **Settings → Data**.

## Browsing fields

Fields are grouped into collapsible cards — **AssetAtlas Core** for built-in fields, then one card per data source for source-specific (extended) fields. Each card shows a count.

- Click a group header to expand or collapse it. Groups start collapsed.
- Use the search box to filter by field name, display name, description, or source. Groups with matches auto-expand while a search is active.
- The stats line shows the total field count, how many are **searchable**, and how many match the current search.

## Editing a field

Core fields are read-only and marked **Core**. Source fields have **Edit** and **Regen** actions:

- **Edit** opens a dialog with two editable attributes: **Display Name** and **Description**. **Save** writes them; nothing else about the field is editable here.
- **Regen** regenerates the field's **description and synonyms** with AI after a confirmation prompt — it replaces both, with no undo. It needs a configured chatbot LLM provider; without one the button is disabled.
- **Regenerate All** (top right) regenerates descriptions and synonyms for *every* data-source field at once, after a confirmation prompt. **AssetAtlas Core** fields are left untouched. It runs in the background and can take a while, and also needs an LLM provider.
- **Auto-generate descriptions with LLM** (top right) is a toggle that lets AssetAtlas fill in missing field descriptions with AI automatically. Like the actions above it needs a configured chatbot LLM provider and is disabled without one.

## Key fields

- **Source** — where the description came from: **Built-in** (hardcoded), **AI Generated** (llm), or **Manual** (edited by hand). Editing a description in the dialog makes it Manual; **Regen** makes it AI Generated.
- **Searchable** — whether the field participates in semantic search (**Yes**/**No**). It's shown here but not editable on this tab.
- **AssetAtlas Core** group — fields with a static type; they're built-in and can't be edited or regenerated.
- **Display Name** — the human-readable label; **Description** and synonyms are what semantic search actually matches against.
