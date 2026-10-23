# Jigx Dynamic Data Recipe Index

Use this index to load the smallest reference needed for the task.

| Task | Read / run |
| --- | --- |
| Log in, pick the org and region, see who `ddcheck` is acting as | `../SKILL.md` (Setup), `ddcheck whoami` |
| Read a solution's tables to disk | `ddcheck dump` |
| Validate menus, config flags and lookups against the solution source | `ddcheck check`; rules in `profile.md` |
| Compare two solutions (QA vs training, dev vs QA) | `ddcheck diff --rows` |
| Copy reference rows into another solution | `ddcheck diff`, then a person runs `ddcheck seed` |
| Write a profile for a customer repo | `profile.md` |
| Management API endpoints, headers, row and page shapes | `dynamic-data-api.md` |
| A feature is on/off for no visible reason, or a flag saved from Manage → Data misbehaves | `ddcheck check` (`bad type`, `missing flag`), and `jigx-mobile-testing` field lessons |

## Skill Boundary

This skill reads and writes Dynamic Data on the server. What the phone holds, and whether a
change reached it, belongs to `jigx-mobile-testing`. Customer table names, counts and tenants
belong in the customer repo's profile, never in this skill.
