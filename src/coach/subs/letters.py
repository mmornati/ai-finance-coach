"""Cancellation letters and e-mails (E8-5), generated LOCALLY from templates: no model, no network, nothing is sent.

    coach subs letter <contract> [--lang fr|it|en] [--channel lrar|email|online]

The text is filled from the contract file (provider, kind, contract number), the household holder's name and the optional
``contact`` block of ``household.yaml`` (postal address, e-mail, phone: LOCAL ONLY, never in any tool output or model context) and
the legal basis of the rules engine (:mod:`coach.skills.cancel`). Whatever is unknown stays a visible [placeholder] to complete.
The user sends the letter themselves. The result also says WHEN it can be sent (not before a free-cancellation window opens, not
after a notice deadline) and what leaving costs, taken from the same rules.

Legal references are limited to the ones in the rules table (each carries its source and review date). The templates are a careful
draft of a customary tone, not legal advice: have a professional read them for a contentious case.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Optional

from coach import disclaimers as D


LANGS = ("fr", "it", "en")
CHANNELS = ("lrar", "email", "online")
MONTHS = {
    "fr": ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"],
    "it": ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre"],
    "en": ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]}


def fmt_date(d: dt.date, lang: str) -> str:
    return f"{d.day}{'er' if d.day == 1 and lang == 'fr' else ''} {MONTHS[lang][d.month - 1]} {d.year}" if lang != "en" else \
        f"{d.day} {MONTHS['en'][d.month - 1]} {d.year}"


KIND_LABEL = {
    "fr": {"energy": "fourniture d'énergie", "telecom": "services de communications électroniques", "insurance_home": "assurance habitation",
           "insurance_car": "assurance automobile", "insurance_health": "complémentaire santé", "health": "complémentaire santé",
           "insurance_other": "contrat d'assurance", "water": "fourniture d'eau",
           "streaming": "abonnement de streaming", "software": "abonnement à un service numérique", "membership": "adhésion",
           "loan_insurance": "assurance emprunteur", "other": "contrat"},
    "it": {"energy": "fornitura di energia", "telecom": "servizi di telecomunicazioni", "insurance_home": "polizza abitazione",
           "insurance_car": "polizza RC auto", "insurance_health": "polizza sanitaria integrativa", "health": "polizza sanitaria integrativa",
           "insurance_other": "polizza assicurativa", "water": "fornitura idrica",
           "streaming": "abbonamento a un servizio di streaming", "software": "abbonamento a un servizio digitale", "membership": "iscrizione",
           "loan_insurance": "assicurazione collegata al mutuo", "other": "contratto"},
    "en": {"energy": "energy supply", "telecom": "electronic communications services", "insurance_home": "home insurance",
           "insurance_car": "car insurance", "insurance_health": "complementary health insurance", "health": "complementary health insurance",
           "insurance_other": "insurance contract", "water": "water supply",
           "streaming": "streaming subscription", "software": "digital service subscription", "membership": "membership",
           "loan_insurance": "borrower insurance", "other": "contract"}}

PLACEHOLDER = {
    "fr": {"name": "[titulaire du contrat]", "address": "[adresse postale]", "provider": "[nom du prestataire]", "number": "[numéro de contrat]",
           "city": "[ville]", "provider_address": "[adresse postale du service résiliation]", "sendno": "[numéro de l'envoi recommandé]"},
    "it": {"name": "[titolare del contratto]", "address": "[indirizzo postale]", "provider": "[nome del fornitore]", "number": "[numero di contratto]",
           "city": "[città]", "provider_address": "[indirizzo del servizio disdette]", "sendno": "[numero della raccomandata]"},
    "en": {"name": "[contract holder]", "address": "[postal address]", "provider": "[provider name]", "number": "[contract number]",
           "city": "[town]", "provider_address": "[provider's cancellation postal address]", "sendno": "[registered mail number]"}}

# ---------------------------------------------------------------- the legal basis sentence per rule and language
BASIS = {
    "fr": {
        "fr-hamon": "Cette résiliation est faite conformément à l'article L113-15-2 du Code des assurances (loi Hamon, loi n° 2014-344 du "
                    "17 mars 2014) : elle prend effet un mois après sa réception par vos services.",
        "fr-ria-sante": "Cette résiliation est faite en application de la résiliation infra-annuelle des contrats de complémentaire santé "
                        "(loi n° 2019-733 du 14 juillet 2019, décret n° 2020-1438) : elle prend effet un mois après sa réception.",
        "fr-chatel-insurance": "Cette décision est notifiée conformément à l'article L113-12 du Code des assurances (préavis prévu au contrat).",
        "fr-lemoine": "En application de l'article L313-30 du Code de la consommation (loi Lemoine, loi n° 2022-270 du 28 février "
                      "2022), je vous demande de prendre acte de la substitution de l'assurance de mon prêt par un contrat présentant "
                      "des garanties équivalentes, que je vous joins.",
        "fr-telecom": "Cette résiliation est faite conformément aux dispositions applicables aux contrats de communications électroniques "
                      "(loi n° 2008-3 du 3 janvier 2008, dite loi Chatel, et Code de la consommation).",
        "fr-energy": "Cette résiliation est faite conformément au libre choix du fournisseur prévu par le Code de l'énergie, et sans frais.",
        "fr-3-clics": "Je vous rappelle que, pour un contrat pouvant être conclu en ligne, l'article L215-1-1 du Code de la "
                      "consommation (loi n° 2022-1158 du 16 août 2022) impose de permettre sa résiliation en ligne et de confirmer au "
                      "consommateur la date de fin du contrat.",
        "fr-chatel-renewal": "Ce contrat ayant été reconduit tacitement, je vous rappelle que l'article L215-1 du Code de la "
                             "consommation (loi n° 2005-67 du 28 janvier 2005) impose au professionnel de rappeler au consommateur sa "
                             "faculté de ne pas reconduire ; à défaut de ce rappel, la résiliation est possible à tout moment après la "
                             "reconduction, avec remboursement de la part non consommée.",
    },
    "it": {
        "it-bersani-telecom": "Il recesso è esercitato ai sensi dell'art. 1 del D.L. 7/2007 (c.d. decreto Bersani), convertito con modificazioni "
                              "dalla L. 40/2007, senza penali, salvo i soli costi giustificati dai costi reali dell'operatore, con preavviso "
                              "non superiore a trenta giorni.",
        "it-bersani-energy": "Il recesso è esercitato ai sensi della disciplina ARERA del mercato retail e del D.L. 7/2007 (c.d. decreto Bersani), "
                             "senza penali, con preavviso non superiore a un mese.",
        "it-rcauto": "Come previsto dall'art. 22 del D.L. 179/2012 (convertito dalla L. 221/2012), il contratto di assicurazione RC auto non "
                     "si rinnova tacitamente: la presente vale come comunicazione di mancato rinnovo.",
        "it-insurance": "La presente disdetta è inviata ai sensi dell'art. 1899 del Codice civile e delle condizioni di polizza, nei termini e "
                        "con le modalità ivi previsti.",
        "it-loan-insurance": "Ai sensi della normativa IVASS applicabile (Regolamenti IVASS n. 40/2018 e n. 41/2018), Vi comunico la "
                             "sostituzione della polizza collegata al mutuo con una polizza di mia scelta, equivalente a quella richiesta, "
                             "e Vi chiedo il rimborso della parte di premio non goduta.",
        "it-subscription": "Ai sensi delle condizioni contrattuali e del Codice del consumo (D.Lgs. 206/2005), Vi comunico la disdetta "
                           "del contratto.",
    },
}
ORDER = {"fr": ["fr-hamon", "fr-ria-sante", "fr-lemoine", "fr-telecom", "fr-energy", "fr-chatel-insurance", "fr-chatel-renewal", "fr-3-clics"],
         "it": ["it-bersani-telecom", "it-bersani-energy", "it-rcauto", "it-insurance", "it-loan-insurance", "it-subscription"]}
# the rules a letter states as ITS basis (the others stay in the notes): the operative ones
OPERATIVE = {"fr-hamon", "fr-ria-sante", "fr-lemoine", "fr-telecom", "fr-energy", "it-bersani-telecom", "it-bersani-energy", "it-rcauto",
             "it-insurance", "it-loan-insurance", "it-subscription"}

T = {
    "fr": {
        "greeting": "Madame, Monsieur,", "to_service": "Service résiliation", "registered": "Lettre recommandée avec accusé de réception n° {sendno}",
        "subject": "Objet : résiliation du contrat n° {number}{kind}", "subject_nonrenewal": "Objet : non-reconduction du contrat n° {number}{kind}",
        "subject_sub": "Objet : demande de substitution de l'assurance emprunteur (contrat n° {number})",
        "intro": "Titulaire du contrat n° {number}{kind}{since}, je vous notifie par la présente ma décision de le résilier{effective}.",
        "intro_nonrenewal": "Titulaire du contrat n° {number}{kind}{since}, je vous notifie par la présente ma décision de ne pas le reconduire{effective}.",
        "intro_sub": "Titulaire du contrat n° {number}{kind}{since}, je vous informe de ma décision de remplacer cette assurance.",
        "since": ", souscrit le {date}", "eff_from": " à compter du {date}", "eff_at": " pour son échéance du {date}",
        "eff_asap": " dans les meilleurs délais permis par le contrat",
        "eff_notice": " avec effet à l'issue du délai de préavis de {n} jours suivant sa réception",
        "ask_head": "Je vous prie de bien vouloir :", "ask_confirm": "me confirmer par écrit la prise en compte de cette demande et sa date d'effet ;",
        "ask_stop": "cesser tout prélèvement à compter de cette date ;", "ask_refund": "me rembourser, le cas échéant, la part de cotisation ou de prime payée d'avance et non consommée.",
        "ask_refund_other": "me rembourser, le cas échéant, la part d'abonnement ou de facturation payée d'avance et non consommée.",
        "ask_equipment": "m'indiquer les modalités de restitution du matériel ;",
        "closing": "Dans l'attente de votre confirmation écrite, je vous prie d'agréer, Madame, Monsieur, l'expression de mes salutations distinguées.",
        "closing_mail": "Dans l'attente de votre confirmation écrite, je vous prie de croire, Madame, Monsieur, en l'expression de mes salutations distinguées.",
        "signature": "Signature :", "place_date": "{city}, le {date}", "email_subject": "Objet : résiliation du contrat n° {number}",
        "online_head": "Message à coller dans le formulaire ou le chat de résiliation", "steps_head": "Étapes",
        "online_msg": "Bonjour, je souhaite résilier mon contrat n° {number}{kind}{effective}. Merci de me confirmer par écrit la date de fin du contrat.",
        "steps": ["Connectez-vous à votre espace client et utilisez le bouton ou le formulaire de résiliation (la résiliation en ligne est "
                  "obligatoire pour un contrat qui peut être souscrit en ligne).",
                  "Conservez la confirmation (capture d'écran et e-mail) avec la date de fin annoncée.",
                  "Vérifiez que les prélèvements s'arrêtent à cette date."],
        "contact": "Coordonnées : {parts}",
    },
    "it": {
        "greeting": "Spett.le {provider},", "to_service": "Ufficio disdette", "registered": "Raccomandata A/R n. {sendno}",
        "subject": "Oggetto: {act} n. {number}{kind}", "subject_nonrenewal": "Oggetto: mancato rinnovo del contratto n. {number}{kind}",
        "subject_sub": "Oggetto: sostituzione della polizza collegata al mutuo (contratto n. {number})",
        "intro": "In qualità di intestatario del contratto n. {number}{kind}{since}, Vi comunico con la presente la mia decisione di "
                 "{verb} stesso{effective}.",
        "intro_nonrenewal": "In qualità di intestatario del contratto n. {number}{kind}{since}, Vi comunico con la presente che non intendo rinnovarlo{effective}.",
        "intro_sub": "In qualità di intestatario del contratto n. {number}{kind}{since}, Vi informo della mia decisione di sostituire questa polizza.",
        "since": ", sottoscritto il {date}", "eff_from": " con effetto dal {date}", "eff_at": " alla sua scadenza del {date}",
        "eff_asap": " nel più breve termine consentito dal contratto",
        "eff_notice": " con effetto allo scadere del preavviso di {n} giorni dalla ricezione della presente",
        "ask_head": "Vi prego di:", "ask_confirm": "confermarmi per iscritto la presa in carico della richiesta e la data di efficacia;",
        "ask_stop": "interrompere ogni addebito a partire da tale data;", "ask_refund": "rimborsarmi, se del caso, la parte di premio pagata anticipatamente e non goduta.",
        "ask_refund_other": "rimborsarmi, se del caso, la parte di canone o di importo pagata anticipatamente e non goduta.",
        "ask_equipment": "indicarmi le modalità di restituzione delle apparecchiature;",
        "closing": "In attesa di un Vostro riscontro scritto, porgo distinti saluti.", "closing_mail": "In attesa di un Vostro riscontro scritto, porgo distinti saluti.",
        "signature": "Firma:", "place_date": "{city}, {date}", "email_subject": "Oggetto: {act} n. {number}",
        "online_head": "Messaggio da incollare nel modulo o nella chat per le disdette", "steps_head": "Passaggi",
        "online_msg": "Buongiorno, desidero {verb} n. {number}{kind}{effective}. Vi prego di confermarmi per iscritto la data di cessazione.",
        "steps": ["Accedete alla vostra area clienti e usate la funzione o il modulo di disdetta/recesso, se disponibile.",
                  "Conservate la conferma (schermata ed e-mail) con la data di cessazione comunicata.",
                  "Verificate che gli addebiti si interrompano a quella data."],
        "contact": "Recapiti: {parts}",
    },
    "en": {
        "greeting": "Dear Sir or Madam,", "to_service": "Cancellations department", "registered": "Registered letter with acknowledgement of receipt no. {sendno}",
        "subject": "Subject: cancellation of contract no. {number}{kind}", "subject_nonrenewal": "Subject: non-renewal of contract no. {number}{kind}",
        "subject_sub": "Subject: substitution of the borrower insurance (contract no. {number})",
        "intro": "As the holder of contract no. {number}{kind}{since}, I hereby give notice that I am cancelling it{effective}.",
        "intro_nonrenewal": "As the holder of contract no. {number}{kind}{since}, I hereby give notice that I will not renew it{effective}.",
        "intro_sub": "As the holder of contract no. {number}{kind}{since}, I inform you that I am replacing this insurance.",
        "since": ", taken out on {date}", "eff_from": ", with effect from {date}", "eff_at": ", at its expiry on {date}",
        "eff_asap": ", as soon as the contract allows",
        "eff_notice": ", taking effect at the end of the {n}-day notice period following its receipt",
        "ask_head": "Please:", "ask_confirm": "confirm in writing that this request has been received and the date it takes effect;",
        "ask_stop": "stop all direct debits from that date;", "ask_refund": "refund, where applicable, any amount paid in advance for a period not used.",
        "ask_refund_other": "refund, where applicable, any amount paid in advance for a period not used.",
        "ask_equipment": "tell me how to return any equipment;",
        "closing": "I look forward to your written confirmation.\nYours faithfully,", "closing_mail": "I look forward to your written confirmation.\nKind regards,",
        "signature": "Signature:", "place_date": "{city}, {date}", "email_subject": "Subject: cancellation of contract no. {number}",
        "online_head": "Message to paste into the cancellation form or chat", "steps_head": "Steps",
        "online_msg": "Hello, I would like to cancel my contract no. {number}{kind}{effective}. Please confirm the end date of the contract in writing.",
        "steps": ["Log in to your customer area and use the cancellation button or form (online cancellation must be offered for a contract that can be taken out online).",
                  "Keep the confirmation (screenshot and e-mail) showing the end date announced.",
                  "Check that the direct debits stop on that date."],
        "contact": "Contact: {parts}",
    },
}
NOTES = {
    "fr": {"not_before": "Ne l'envoyez pas avant le {date} : avant cette date la résiliation gratuite n'est pas ouverte.",
           "send_by": "Envoyez-la au plus tard le {date} (préavis).", "deadline_passed": "La date limite de préavis ({date}) est dépassée : "
                                                                                   "la résiliation ne prendra effet qu'à l'échéance suivante, ou vérifiez les exceptions du contrat.",
           "early_cost": "Quitter maintenant coûte environ {amount} EUR ({months} mois restants, {share} de l'abonnement restant dû) ; la sortie est gratuite à partir du {free}.",
           "foreign_basis": "Attention : le contrat relève du droit {country} ; les références légales du texte sont celles de ce pays, citées dans sa langue ({native}).",
           "rcauto": "En Italie, l'assurance RC auto ne se renouvelle pas tacitement : cette lettre n'est pas obligatoire ; souscrivez la nouvelle police avant l'échéance (la garantie dure encore 15 jours).",
           "energy": "Ne résiliez pas avant d'avoir souscrit chez le nouveau fournisseur, qui résilie l'ancien contrat pour vous : pas d'interruption de fourniture.",
           "lrar": "Envoyez-la en recommandé avec accusé de réception et gardez le justificatif de dépôt ; complétez les [champs] entre crochets.",
           "email": "Envoyez-la depuis l'adresse e-mail du contrat, gardez l'accusé d'envoi et complétez les [champs] entre crochets.",
           "online": "Utilisez le formulaire ou le bouton de résiliation en ligne, gardez la confirmation.",
           "unknown": "Il manque dans le contrat : {fields}. Complétez-les avant d'envoyer.",
           "lemoine": "La nouvelle police doit présenter des garanties équivalentes ; le prêteur répond sous 10 jours ouvrés. Joignez l'offre de la nouvelle assurance.",},
    "it": {"not_before": "Non inviarla prima del {date}: prima di tale data il recesso gratuito non è ancora aperto.",
           "send_by": "Inviala al più tardi entro il {date} (preavviso).", "deadline_passed": "Il termine di preavviso ({date}) è scaduto: "
                                                                                      "la disdetta avrà effetto alla scadenza successiva; verifica le eccezioni del contratto.",
           "early_cost": "Uscire ora costa circa {amount} EUR ({months} mesi rimanenti, {share} del canone ancora dovuto); l'uscita è gratuita dal {free}.",
           "foreign_basis": "Attenzione: il contratto è soggetto al diritto {country}; i riferimenti normativi del testo sono quelli di quel paese, citati nella sua lingua ({native}).",
           "rcauto": "L'RC auto non si rinnova tacitamente: questa lettera non è necessaria, serve solo se vuoi comunicare il mancato rinnovo; acquista la nuova polizza prima della scadenza (la copertura prosegue 15 giorni).",
           "energy": "Non recedere prima di aver sottoscritto con il nuovo fornitore, che comunica il recesso al vecchio: nessuna interruzione della fornitura.",
           "lrar": "Inviala con raccomandata A/R e conserva la ricevuta; completa i [campi] tra parentesi quadre.",
           "email": "Inviala dall'indirizzo e-mail del contratto (o via PEC), conserva la ricevuta e completa i [campi] tra parentesi quadre.",
           "online": "Usa il modulo o la funzione di disdetta online e conserva la conferma.",
           "unknown": "Nel contratto mancano: {fields}. Completali prima di inviare.",
           "lemoine": "La nuova polizza deve essere equivalente a quella richiesta dalla banca. Allega la proposta della nuova assicurazione.",},
    "en": {"not_before": "Do not send it before {date}: free cancellation is not open before then.",
           "send_by": "Send it no later than {date} (notice).", "deadline_passed": "The notice deadline ({date}) has passed: "
                                                                                 "cancellation will only take effect at the following expiry; check the contract for exceptions.",
           "early_cost": "Leaving now costs about {amount} EUR ({months} months left, {share} of the subscription still due); leaving is free from {free}.",
           "foreign_basis": "Note: the contract is under {country} law; the legal references in the text are that country's, quoted in its language ({native}).",
           "rcauto": "Motor third-party policies in Italy do not renew tacitly: this letter is not required, it only informs the insurer; buy the new policy before the expiry (the cover lasts 15 more days).",
           "energy": "Do not cancel before signing with the new supplier, who cancels the old contract for you: no gap in supply.",
           "lrar": "Send it by registered post with acknowledgement of receipt and keep the receipt; complete the [fields] in brackets.",
           "email": "Send it from the e-mail address on the contract, keep the sent copy and complete the [fields] in brackets.",
           "online": "Use the online cancellation form or button and keep the confirmation.",
           "unknown": "The contract file lacks: {fields}. Complete them before sending.",
           "lemoine": "The new policy must offer equivalent guarantees; the lender answers within 10 working days. Attach the new insurer's offer.",},
}
for _lang in NOTES:                      # the closing "verify" note of every letter lives with the other disclaimers (E11-5)
    NOTES[_lang]["verify"] = D.get("letter_verify", _lang)


def _ph(lang, key):
    return PLACEHOLDER[lang][key]


def _basis_lines(lang: str, res: dict, today: dt.date) -> tuple[list[str], list[dict]]:
    ids = [r["id"] for r in res["rules"]]
    used, lines = [], []
    renewed = any("renewed on" in c for c in res.get("conditions", []))
    pick = [i for i in ids if i in OPERATIVE]
    # an anniversary route (non-renewal) is stated as its own rule when the free window is not open yet
    nonrenew = res["can_cancel_now"] is False and res.get("anniversary_route") is not None and "fr-chatel-insurance" in ids
    if nonrenew:
        pick = ["fr-chatel-insurance"]
    elif "fr-chatel-renewal" in ids and renewed:
        pick = pick + ["fr-chatel-renewal"]
    if "fr-3-clics" in ids and not nonrenew:
        pick = pick + ["fr-3-clics"]
    if lang == "en":
        for r in res["rules"]:
            if r["id"] not in pick:
                continue
            if r["id"] == "fr-3-clics":
                lines.append(f"As this contract may have been taken out online, please confirm its end date in writing, as required for online termination ({r['law']}).")
            elif r["id"] == "fr-chatel-renewal":
                lines.append(f"The contract was tacitly renewed: please note the provider's duty to remind the consumer before renewal ({r['law']}).")
            elif r["id"] == "fr-chatel-insurance":
                lines.append(f"This notice of non-renewal is given in accordance with {r['law']}.")
            else:
                lines.append(f"This notice is given in accordance with {r['law']}.")
            used.append(r)
        return lines, used
    native = "fr" if res["country"] == "FR" else "it"           # the legal references are those of the contract's country, quoted in its language
    table = BASIS[native]
    order = ORDER[native]
    for rid in sorted(set(pick), key=lambda i: order.index(i) if i in order else 99):
        if rid in table:
            lines.append(table[rid])
            used.append(next(r for r in res["rules"] if r["id"] == rid))
    return lines, used


def build(contract, res: dict, *, today: dt.date, lang: str = "fr", channel: str = "lrar", holder_name: Optional[str] = None,
          contact: Optional[dict] = None, family: Optional[str] = None) -> dict:
    """Render the letter. ``contract``: a memory ``Contract``; ``res``: its :func:`coach.skills.cancel.cancellability` result;
    ``contact``: ``{address, email, phone}`` or None. Returns the text, the placeholders still to fill and the notes."""
    if lang not in LANGS:
        raise ValueError(f"lang must be one of {', '.join(LANGS)}")
    if channel not in CHANNELS:
        raise ValueError(f"channel must be one of {', '.join(CHANNELS)}")
    t, notes_t = T[lang], NOTES[lang]
    contact = contact or {}
    fam = family or res["family"]
    kind_key = "loan_insurance" if fam == "loan_insurance" else (contract.kind or "other")
    kind = KIND_LABEL[lang].get(kind_key, KIND_LABEL[lang]["other"]) if kind_key != "other" else ""
    placeholders: list[str] = []

    def val(v, key, label):
        if v and str(v).strip():
            return str(v).strip()
        placeholders.append(label)
        return _ph(lang, key)
    name = val(holder_name, "name", "holder name (a household member, or --holder)")
    provider = val(contract.provider, "provider", "provider")
    number = val(contract.contract_number, "number", "contract_number")
    address = val(contact.get("address"), "address", "household.yaml contact.address")
    email = (contact.get("email") or "").strip()
    phone = (contact.get("phone") or "").strip()
    eff = res["earliest_effective_date"]
    can = res["can_cancel_now"]
    ar = res.get("anniversary_route")
    nonrenew = can is False and ar is not None and fam in ("insurance_home", "insurance_car", "insurance_health", "insurance_other")
    rcauto = res["country"] == "IT" and fam == "insurance_car"
    notes: list[str] = []
    send_after = res.get("first_request_date") if can is False and not nonrenew else None
    send_by = None
    # -- the effective date clause
    if nonrenew:
        eff_clause = t["eff_at"].format(date=fmt_date(ar["effective"], lang))
        send_by = ar["send_notice_by"]
    elif can is False and eff is not None:
        eff_clause = t["eff_at"].format(date=fmt_date(eff, lang)) if (res.get("conditions") or rcauto) else t["eff_from"].format(date=fmt_date(eff, lang))
        if rcauto:
            send_by = None
    elif can and res.get("notice_period_days"):
        eff_clause = t["eff_notice"].format(n=res["notice_period_days"])          # the date counts from the RECEIPT: not a fixed date
    else:
        eff_clause = t["eff_asap"]
    if rcauto:
        nonrenew = True
    cnd = res.get("contract_notice_deadline")
    if cnd and not send_by:
        send_by = cnd["send_notice_by"]
    since = ""                                      # a drafted contract's start_date is only the first payment seen: never asserted
    disdetta = fam.startswith("insurance") or fam == "loan_insurance"
    act = "disdetta del contratto" if disdetta else "recesso dal contratto"                   # Italian wording (other languages ignore it)
    verb = "disdire il contratto" if disdetta else "esercitare il recesso dal contratto"
    # -- the pieces
    basis, used = _basis_lines(lang, res, today)
    intro_key = "intro_sub" if fam == "loan_insurance" else ("intro_nonrenewal" if nonrenew else "intro")
    subject_key = "subject_sub" if fam == "loan_insurance" else ("subject_nonrenewal" if nonrenew else "subject")
    fmtargs = dict(number=number, kind=f" ({kind})" if kind else "", since=since, effective=eff_clause, act=act, verb=verb, provider=provider)
    intro = t[intro_key].format(**fmtargs)
    asks = [t["ask_confirm"]] + ([] if fam == "loan_insurance" else [t["ask_stop"]]) + \
           ([t["ask_equipment"]] if fam == "telecom" else []) + \
           [t["ask_refund"] if fam.startswith("insurance") or fam == "loan_insurance" else t["ask_refund_other"]]
    # the last item ends with a full stop, the others with ';' (a list)
    asks = [a.rstrip(";.") + (";" if i < len(asks) - 1 else ".") for i, a in enumerate(asks)]
    city = _ph(lang, "city")
    placeholders.append("town / city")
    dline = t["place_date"].format(city=city, date=fmt_date(today, lang))
    contact_parts = [p for p in (address if address != _ph(lang, "address") else None, email, phone) if p]
    sender = [name] + (address.splitlines() if address else []) + ([email] if email else []) + ([phone] if phone else [])
    subject = t[subject_key].format(**fmtargs)
    # -- assemble by channel
    if channel == "online":
        msg = t["online_msg"].format(**fmtargs)
        body = [t["online_head"] + ":", "", msg, ""] + ([basis[0]] if basis else []) + ["", t["steps_head"] + ":"] + \
               [f"{i}. {s}" for i, s in enumerate(t["steps"], 1)]
        text = "\n".join(body)
    elif channel == "email":
        body = [t["email_subject"].format(**fmtargs), "", t["greeting"].format(provider=provider), "", intro, ""] + [b + "\n" for b in basis] + \
               [t["ask_head"]] + [f"- {a}" for a in asks] + ["", t["closing_mail"], "", name, address] + ([email] if email else []) + \
               ([phone] if phone else [])
        text = re.sub(r"\n{3,}", "\n\n", "\n".join(body))
        placeholders = [p for p in placeholders if p != "town / city"]
    else:
        head = sender + ["", provider, t["to_service"], _ph(lang, "provider_address"), "", dline, "", t["registered"].format(sendno=_ph(lang, "sendno")),
                         "", subject, "", t["greeting"].format(provider=provider), "", intro, ""]
        body = head + [b + "\n" for b in basis] + [t["ask_head"]] + [f"- {a}" for a in asks] + ["", t["closing"], "", t["signature"], "", "", name]
        text = re.sub(r"\n{3,}", "\n\n", "\n".join(body))
        placeholders += ["provider's cancellation address", "registered mail number"]
    # -- notes: when to send, what leaving costs, what is missing
    if send_after and send_after > today:
        notes.append(notes_t["not_before"].format(date=fmt_date(send_after, lang)))
    if send_by:
        notes.append((notes_t["send_by"] if send_by >= today else notes_t["deadline_passed"]).format(date=fmt_date(send_by, lang)))
    ec = res.get("early_termination_cost")
    if ec:
        amount = f"{ec['amount']:.2f}" if ec.get("amount") is not None else "?"
        share = f"{ec['share_pct']} %" if ec.get("share_pct") is not None else "?"
        notes.append(notes_t["early_cost"].format(amount=amount, months=ec["remaining_months"], share=share,
                                                  free=fmt_date(ec["free_exit_date"], lang)))
    if rcauto:
        notes.append(notes_t["rcauto"])
    if fam == "energy":
        notes.append(notes_t["energy"])
    if fam == "loan_insurance":
        notes.append(notes_t["lemoine"])
    if res.get("unknown"):
        notes.append(notes_t["unknown"].format(fields=", ".join(res["unknown"])))
    native = "fr" if res["country"] == "FR" else "it"
    if lang not in (native, "en"):
        notes.append(notes_t["foreign_basis"].format(country=res["country"], native=native))
    notes.append(notes_t[channel])
    notes.append(notes_t["verify"])
    placeholders = list(dict.fromkeys(p for p in placeholders if p))
    slug = re.sub(r"[^a-z0-9]+", "-", (contract.id or "contract").lower()).strip("-")
    return {"contract": contract.id, "lang": lang, "channel": channel, "subject": subject.split(": ", 1)[-1] if ": " in subject else subject,
            "text": text.strip() + "\n", "filename": f"cancellation-{slug}-{channel}-{lang}.txt", "placeholders": placeholders,
            "legal_basis": [{"id": r["id"], "name": r["name"], "law": r["law"], "source": r["source"], "last_reviewed": r["last_reviewed"]}
                            for r in used], "can_cancel_now": can, "send_on_or_after": send_after, "send_by": send_by, "notes": notes,
            "pdf": None, "pdf_note": "text only: no PDF writer is installed (pypdf reads and merges PDFs but does not lay out text)",
            "sent": False}
