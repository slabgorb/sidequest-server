# SOUL.md — SideQuest Engine Guidelines

Rules that govern how the server code must treat authored worlds.

<purpose>
The server is the faithful reader of authored worlds — strict at the boundary, transparent at the seam, observable at every decision, and never an author in disguise.
</purpose>

**Content is canon. Code is the reader.**  The YAML in `sidequest-content/` is the source of truth for every world, character, culture, trope, and legend. The engine reads, interprets, and presents. It never writes back to authored content, never infers what "probably should be there," never silently substitutes a default for a missing field. If a world doesn't load, the world doesn't exist. Authors are trusted to have meant what they wrote; engines are suspect until proven faithful.

**Inheritance is the spine.**  base → genre → world. Every load walks that chain. A genre pack is a rulebook; a world is a campaign setting in that rulebook; base is the grammar both speak. Code that reads one layer in isolation is reading a fragment, not a world. Shortcuts — reading a YAML file directly, caching a partial merge, assuming flat structure, skipping `_from:` pointer resolution — are bugs even when they compile.

**Strictness surfaces drift.**  `extra: forbid` is the lie detector for content. When a field appears in authored YAML but nowhere in the model, one of two things is true: the author wrote something the engine silently ignores, or the engine was built on an assumption that has decayed. Both deserve to fail loudly. Pass-through fields are IOUs — each one is a promise to either wire the field or delete it. Not permanent decoration.

**Fail loud at the boundary.**  Missing file: error with the path. Malformed field: error with the line. Broken pointer: error with the pointer and the target. The loader is the airlock; anything beyond it has been vetted, so downstream code may trust its inputs. Silent fallbacks past the boundary are Trojan horses — they defer pain to a debugger three days later instead of paying it at the moment of harm. Loud failure is kindness.

**The loader is the contract.**  One function — `load_genre_pack()` — is the only sanctioned reader of authored content. If code elsewhere reads YAML directly, it has forked the strictness policy, the inheritance walk, the pointer-resolution rules, and the error language. That fork is always wrong, even when it "works." One entry point, one inheritance path, one strictness stance.

**Files are canon; author memory drifts.**  When what the author remembers diverges from what the author wrote, the files win. Years pass; campaigns accumulate; only the pack on disk doesn't rot. "I think this used to work like X" is weaker evidence than the YAML in front of you. Read the files. Trust the files. Update the files when they're wrong; don't patch the engine to match a memory.

**Observability is non-negotiable.**  Every subsystem decision emits an OTEL span with enough payload to reconstruct the decision. Spans are to the engine what strict models are to content: the lie detector. Without them, there is no way to tell whether a subsystem engaged or whether the narrator improvised around it. No span, no trust. Observability is not debugging scaffolding to be stripped in production — it is the production contract.

**Play promotes coal; play is not authorship.**  Save files are derived state. They record what happened in a session: positions, inventories, events, chargen choices, the shape of the fiction at this point in the chronology. They never write back to `sidequest-content/`. When a session surfaces a detail worth keeping — a minor NPC the players care about, a named location they polished into a diamond — that detail's path to canon is *deliberate promotion*: a human or tooling decision to lift it into authored content. Automatic back-propagation from session to pack is the most dangerous shortcut in the system, and it is forbidden.

**The Test.**  The engine never invents. If it's in the world, an author wrote it or a player did it. Anything else is improvisation, and improvisation belongs to the narrator — not the engine.
