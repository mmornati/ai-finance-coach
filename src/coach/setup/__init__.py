"""Packaging and first-run (E13): ``coach init``, ``coach doctor``, ``coach setup`` (the wizard) and ``coach setup enablebanking``.

Nothing here sends anything by itself: the steps that reach the outside (the Enable Banking check, a bank connection, a sync, a model
labelling run) happen only when the person at the keyboard says yes at that step, and every one goes through the existing commands
(and so through ``egress.allow``).
"""
