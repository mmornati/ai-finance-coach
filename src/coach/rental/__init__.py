"""Rental property under a tax-incentive scheme (E15): the property account and its categories, the monthly cash flow and yearly P&L
(with the effort d'epargne and the vacancy), the scheme commitment and its reminders, the tax-year figures of the rental-income return and
the renegotiate-or-sell indicators.

Everything is computed from the bank data, the memory (``assets.yaml``: a ``real_estate_rental`` asset and its ``commitment``) and the loan
schedule of E9. A fact that is not recorded stays missing (an open question), a figure the user typed (rent cap, income limit, rates, market
rate, valuation) is used as given and never looked up. Docs: ``docs/rental.md``."""
