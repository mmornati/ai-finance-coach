# Choosing the licence (the owner's decision)

> **Decided on 2026-10-06: MIT.** The text is in `LICENSE` and `pyproject.toml` says `license = "MIT"`. The rest of this file is the
> comparison that informed the decision, kept as a record.

The project is **not licensed yet**: there is no `LICENSE` file, `pyproject.toml` has no `license`, and `coach dev release-check` refuses to pass
(and CI stays red) until you decide. This file explains the two candidates so that the decision is informed. It is not legal advice; if the
project will be used commercially or you contribute as an employee, ask a lawyer (some employment contracts give the employer rights to side projects).

Until a licence is published, the law's default applies: **all rights reserved**. People may read the code on a public host but may not copy,
modify or redistribute it. That is almost never what an open-source release intends.

## The two options

| | **MIT** | **AGPL-3.0** (`AGPL-3.0-or-later` or `-only`) |
|---|---|---|
| Kind | Permissive | Strong copyleft, with a network clause |
| Others may use it privately, for money, in companies | Yes | Yes |
| Others may modify and keep their changes private | Yes (even in a closed product) | **Only for private use.** If they distribute it, or let people use a modified version over a network (a hosted service), they must offer those users the complete source of their version under the AGPL |
| Others may include it in a closed-source product | Yes | No: the combined work must be AGPL |
| Obligation on users | Keep the copyright and licence notice | Keep notices; share source of modified versions they distribute or run as a service; state changes |
| Patent grant | No explicit grant | Explicit patent licence from contributors |
| Compatibility | Combines with almost anything | Combines with GPL-3.0 / AGPL-3.0; not with some permissive-only or GPL-2.0-only projects; fine to *use* MIT / BSD / Apache-2.0 dependencies |
| Adoption | Widest; companies adopt without review | Narrower; many companies forbid AGPL code |
| Typical choice for | Libraries, tools you want everywhere | Self-hosted apps that you do not want turned into someone else's closed SaaS |

## What it means for *this* project

* **A hosted copy.** This is a self-hosted app for one household. With MIT, a company could take it, add features, host it as a paid service and
  never share the changes. With AGPL they would have to publish their modified source. (Separately, Enable Banking's terms forbid making its API
  "accessible to any third party" through a shared application: a multi-tenant service built on this code would need its own agreement with them
  whatever the licence says. See `docs/research/notes/demand_and_legal.md`.)
* **Closest projects.** The most complete overlapping open-source project in the project's own research is AGPL-licensed; the others are mostly MIT.
  Staying AGPL keeps this project's improvements shared if someone forks it; MIT keeps it frictionless for individuals and small tools.
* **Contributors.** Under either licence, contributions come in under the same licence ("inbound = outbound") unless you set up a CLA. Do not take a
  CLA lightly: it deters contributors. If you might ever want to relicense (for example to dual-license commercially), only the AGPL keeps that open
  *because you hold the copyright of everything you wrote*; contributions from others would then need their agreement.
* **Dependencies.** Check that every dependency is compatible with your choice (`uv tree`; the main ones are permissive: FastAPI, pydantic, requests,
  PyJWT, cryptography, anthropic, PyYAML, ruamel.yaml, uvicorn, mcp, keyring, pypdf, SQLCipher (BSD-style) through `sqlcipher3-wheels`). Some wheels
  bundle native libraries: read their licence files. This has **not** been audited for you.
* **Bank data.** The licence covers the code only. It says nothing about the privacy of the data the app handles; that is in `docs/privacy.md`.

## A short way to decide

* You want the code used as widely as possible and do not mind closed forks: **MIT**.
* You want improvements to come back, and to prevent a closed hosted clone: **AGPL-3.0-or-later**.
* You are unsure: AGPL can later be relaxed to MIT by you alone while you are the only copyright holder; going the other way is harder once others
  have contributed under MIT. (Relicensing needs the agreement of every other contributor.)

## After you choose

1. Save the official licence text as `LICENSE` at the root (MIT: <https://opensource.org/license/mit> with your name and the year;
   AGPL-3.0: <https://www.gnu.org/licenses/agpl-3.0.txt>).
2. In `pyproject.toml`: delete the `TODO(license)` comment block and set
   `license = "MIT"` or `license = "AGPL-3.0-or-later"` plus `license-files = ["LICENSE"]`; add the matching trove classifier only if you want it
   (SPDX in `license` is preferred).
3. In `README.md`: replace the "License" section (it carries the `TODO(license)` marker) by one line naming the licence and linking `LICENSE`.
4. Optional: an SPDX header (`# SPDX-License-Identifier: ...`) at the top of source files; a `NOTICE` for bundled third-party assets.
5. Say in `CONTRIBUTING.md` that contributions are licensed under the project's licence.
6. Run `uv run coach dev release-check`. The licence checks then pass; the other checks of `docs/release.md` still apply.
7. You may delete this file, or keep it as a record of the decision.
