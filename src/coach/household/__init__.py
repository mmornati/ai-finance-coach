"""Household & people (E14): members, who owns what, who a transaction belongs to, the children's money, who pays what.

* :mod:`coach.household.people`       members from ``household.yaml``, owner values -> member ids, the rules read from memory
* :mod:`coach.household.attribution`  transaction -> member: manual reassignment > memory rules > account owner (E14-3)
* :mod:`coach.household.kids`         pocket money, extra top-ups, spending, balance trend of a child (E14-5)
* :mod:`coach.household.kidbudgets`   weekly / monthly limits and the gentle alerts (E14-6)
* :mod:`coach.household.allocation`   shared costs split by rule: the "who pays what" view (E14-9)
* :mod:`coach.household.users`        per-user logins, roles, preferences, audit (E14-8)

Money is integer cents inside, decimal strings in every ``to_dict()`` (the rule of :mod:`coach.analytics.common`).
"""
