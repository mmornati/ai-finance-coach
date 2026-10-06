# Security policy

This software handles bank data, so security reports are taken seriously and handled in private.

## Reporting a vulnerability

**Do not open a public issue.** Use GitHub's private vulnerability reporting for this repository
(<https://github.com/mmornati/ai-finance-coach/security/advisories/new>: only the maintainer sees it) and include:

* what you found and why it matters (what an attacker could read, change or send),
* the version (`coach --version`) and how you run it (installed package, Docker, source; macOS or Linux),
* the smallest steps to reproduce it, using **invented data only**. Never send real bank data, keys, a database or a backup.

You will get an acknowledgement, a first assessment, and a fix or a mitigation plan; you will be credited in the changelog if you wish. Please allow a
reasonable time for a fix before disclosing publicly. This is a small project maintained by volunteers: there is no bug bounty and no guaranteed response time.

## Scope

In scope: the code in this repository, the Docker files, the documentation's security claims (a claim that is false is a vulnerability).

Of particular interest: anything that

* leaks personal or financial data outside the machine, or into a model prompt, a log or the egress journal (see `docs/privacy.md`);
* lets a model, a merchant name, a document or a web page cause an action (the prompt-injection boundary of `docs/security.md`);
* lets an agent or a local process accept a memory proposal, bypass a typed confirmation, read a secret or reach the web app without its one-time login;
* weakens the encryption, the key handling, the file permissions or the loopback-only binding;
* makes the Docker image run as root, publish on a non-loopback address, or expose a secret.

Out of scope: vulnerabilities of Enable Banking, of your bank, of Anthropic or of other third parties (report them to the vendor); attacks that need
you to have already given the attacker your Keychain, your unlocked session or root; the residual risks the project lists openly in
[docs/security.md](docs/security.md) section 6; findings that rely on running the app on a shared machine or an open network against the documentation's advice.

## Supported versions

Only the latest released version receives fixes while the project is below 1.0.

## If you think your own data leaked

Follow section 7 of [docs/security.md](docs/security.md) ("If something leaks"): revoke the bank consents (`coach wipe --dry-run` lists what exists),
rotate the keys, delete the exposed copies, and revoke the application at Enable Banking.

## For contributors

The review checklist in [CONTRIBUTING.md](CONTRIBUTING.md) covers the privacy and safety rules. `coach security audit` and `coach dev hygiene` are
the project's own checks; run them before you open a pull request.
