"""Every disclaimer and legal label of the coach, in one place (E11-5), in English, French and Italian.

Skills, tools, letters, the tax helper, the coach's system prompt, the web app and the CLI all import their wording from here, so
a legal text is changed once. Keys:

* ``ai_label``           "AI-generated" transparency label (EU AI Act art. 50): on every LLM-written text shown to the user
* ``investment_banner``  the banner of an answer flagged by :mod:`coach.compliance` (no personalised investment advice)
* ``general_advice``     the closing line of an answer about saving / investing
* ``tax``                tax reminders (FR / IT, per country)
* ``contract``           contract-law summaries (cancellation rules)
* ``loan``               loan / mortgage estimates
* ``savings``            cheaper-offer estimates
* ``letters``            cancellation letters (drafts)

``get(key, lang)`` falls back to English for an unknown language. The English texts are the reference: a test pins that FR and IT
exist for every key.
"""
from __future__ import annotations

LANGS = ("en", "fr", "it")

AI_LABEL = {
    "en": "AI-generated content: it can contain mistakes. Check the figures against your accounts.",
    "fr": "Contenu généré par une IA : il peut contenir des erreurs. Vérifiez les chiffres dans vos comptes.",
    "it": "Contenuto generato da un'IA: può contenere errori. Verifica le cifre sui tuoi conti.",
}
AI_LABEL_SHORT = {"en": "AI-generated", "fr": "Généré par IA", "it": "Generato da IA"}

INVESTMENT_BANNER = {
    "en": "General information only, not personalised investment advice (FR: AMF / CIF; IT: Consob). "
          "For a specific product, ask a regulated adviser.",
    "fr": "Information générale uniquement, pas un conseil en investissement personnalisé (AMF / CIF). "
          "Pour un produit précis, adressez-vous à un conseiller réglementé.",
    "it": "Solo informazione generale, non consulenza personalizzata in materia di investimenti (Consob). "
          "Per un prodotto specifico rivolgiti a un consulente abilitato.",
}

GENERAL_ADVICE = {
    "en": "This is general information, not financial advice.",
    "fr": "Ceci est une information générale, pas un conseil financier.",
    "it": "Questa è un'informazione generale, non una consulenza finanziaria.",
}

TAX = {
    "en": "General information, not tax advice: rules, rates and ceilings change every year and depend on your situation. Verify on the "
          "official site (FR: impots.gouv.fr; IT: Agenzia delle Entrate) or with an adviser before declaring anything. Nothing is filed "
          "by the coach.",
    "fr": "Information générale, pas un conseil fiscal : les règles, taux et plafonds changent chaque année et dépendent de votre "
          "situation. Vérifiez sur impots.gouv.fr (ou auprès d'un conseiller) avant de déclarer quoi que ce soit. Le coach ne dépose rien.",
    "it": "Informazione generale, non consulenza fiscale: regole, aliquote e massimali cambiano ogni anno e dipendono dalla tua "
          "situazione. Verifica sul sito dell'Agenzia delle Entrate (o con un CAF / commercialista) prima del 730. Il coach non presenta nulla.",
}
# the per-country texts the tax helper has always printed (FR / IT bilingual): kept word for word
TAX_BY_COUNTRY = {
    "FR": ("Information generale, pas un conseil fiscal. Not tax advice: the rules, rates and ceilings change every year and "
           "depend on your situation. Verify on impots.gouv.fr (or with a tax advisor) before declaring anything. Nothing is filed "
           "by the coach."),
    "IT": ("Informazione generale, non consulenza fiscale. Not tax advice: rates, ceilings and conditions change every year and "
           "depend on your situation. Verify on the Agenzia delle Entrate website or with a CAF / commercialista before "
           "the 730. Nothing is filed by the coach."),
}

CONTRACT = {
    "en": "General summary of consumer-law rules, not legal advice: the contract's own terms and your situation decide. "
          "Verify with your contract and the official source (FR: service-public.fr / your insurer or operator; "
          "IT: ARERA, AGCOM, IVASS, your provider) before sending anything.",
    "fr": "Résumé général des règles du droit de la consommation, pas un avis juridique : les termes de votre contrat et votre situation "
          "décident. Vérifiez avec votre contrat et la source officielle (service-public.fr, votre assureur ou opérateur) avant d'envoyer quoi que ce soit.",
    "it": "Sintesi generale delle regole del diritto dei consumatori, non consulenza legale: contano le condizioni del tuo contratto e la tua "
          "situazione. Verifica con il contratto e la fonte ufficiale (ARERA, AGCOM, IVASS, il tuo fornitore) prima di inviare qualsiasi cosa.",
}
CONTRACT_VERIFY = {
    "en": "Verify with your contract and the official source before acting: this is a general summary, not legal advice.",
    "fr": "Vérifiez avec votre contrat et la source officielle avant d'agir : résumé général, pas un avis juridique.",
    "it": "Verifica con il contratto e la fonte ufficiale prima di agire: sintesi generale, non consulenza legale.",
}

LOAN = {
    "en": "Estimate from the figures you gave, not an offer: your bank or a broker gives the binding numbers. "
          "General information, not financial advice; no product is recommended.",
    "fr": "Estimation à partir des chiffres fournis, pas une offre : votre banque ou un courtier donne les chiffres qui engagent. "
          "Information générale, pas un conseil financier ; aucun produit n'est recommandé.",
    "it": "Stima basata sui dati forniti, non un'offerta: la tua banca o un intermediario dà le cifre vincolanti. "
          "Informazione generale, non consulenza finanziaria; nessun prodotto è raccomandato.",
}

SAVINGS = {
    "en": "Estimate from the prices given; verify the offer, its conditions and the date of its price before switching.",
    "fr": "Estimation à partir des prix fournis ; vérifiez l'offre, ses conditions et la date de son prix avant de changer.",
    "it": "Stima basata sui prezzi forniti; verifica l'offerta, le sue condizioni e la data del prezzo prima di cambiare.",
}

LETTERS = {
    "en": "A draft of a customary tone, not legal advice: have a professional read it for a contentious case. Check every [placeholder] "
          "before sending.",
    "fr": "Un brouillon de ton courant, pas un avis juridique : faites-le relire par un professionnel en cas de litige. Complétez chaque "
          "[champ] avant l'envoi.",
    "it": "Una bozza di tono usuale, non consulenza legale: falla leggere a un professionista in caso di contenzioso. Completa ogni "
          "[campo] prima dell'invio.",
}

LETTER_VERIFY = {
    "en": "Draft generated locally from general consumer-law rules: verify with your contract and the official source before sending. "
          "Nothing was sent.",
    "fr": "Brouillon généré localement à partir des règles générales du droit de la consommation : vérifiez avec votre contrat et la source "
          "officielle avant l'envoi. Aucun envoi n'a été fait.",
    "it": "Bozza generata localmente a partire da regole generali di diritto dei consumatori: verifica con il contratto e la fonte ufficiale "
          "prima dell'invio. Nulla è stato inviato.",
}

TEXTS = {"ai_label": AI_LABEL, "ai_label_short": AI_LABEL_SHORT, "investment_banner": INVESTMENT_BANNER,
         "general_advice": GENERAL_ADVICE, "tax": TAX, "contract": CONTRACT, "contract_verify": CONTRACT_VERIFY, "loan": LOAN,
         "savings": SAVINGS, "letters": LETTERS, "letter_verify": LETTER_VERIFY}


def get(key: str, lang: str = "en") -> str:
    """The disclaimer `key` in `lang` (en | fr | it); English for an unknown language."""
    t = TEXTS[key]
    return t.get((lang or "en").lower()[:2], t["en"])


def all_languages(key: str) -> dict[str, str]:
    return dict(TEXTS[key])
