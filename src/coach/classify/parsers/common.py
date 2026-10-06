"""Shared building blocks of the per-bank descriptor parsers: the input/output structures, merchant-key
normalisation, holder / household detection, and the person-vs-company heuristics."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

FX_RE = re.compile(r"\s+(\d+,\d{2})\s+([A-Z]{3})\b.*$")
PERSON_RE = re.compile(r"^(MR OU MME|M OU MME|MME|MLLE|MR|M\.?)\s", re.I)

# Payment processors that prefix the real merchant as "PSP*MERCHANT" (Mol = Mollie).
PROCESSORS = {"MOL", "SUMUP", "SQ", "NYX", "SUNDAY", "PAYPAL", "ZTL", "STRIPE", "IZ", "PAYPLUG", "LYF"}

TITLES = {"M", "MR", "MME", "MLLE", "OU", "MONSIEUR", "MADAME", "SIG", "SIGRA", "SIGNORA", "SIGNOR", "DOTT",
          "DOTTSSA", "MRS", "MISS", "MS"}
# words that prefix the counterparty of a transfer without being part of its name (H3: SEPA/INST left over)
PREFIX_WORDS = {"SEPA", "INST", "INSTANTANE", "WERO", "RECU", "RECUS", "EMIS", "EMISE", "PERMANENT", "OCCASIONNEL",
                "SCT", "SDD", "VIR", "VIREMENT"}
# words that make an account "name" a product label (Visa Premier, Livret A...), not a person
PRODUCT_WORDS = {"VISA", "PREMIER", "CARTE", "CARD", "COMPTE", "CPT", "LIVRET", "MASTERCARD", "GOLD", "PEL", "PEA",
                 "CCP", "CHEQUES", "CHEQUE", "COURANT", "EPARGNE", "DEPOT", "CREDIT", "PRET", "PROFESSIONNEL",
                 "JOINT", "PERSONAL", "CURRENT", "SAVINGS", "SAVING", "POCKET", "EUR", "USD", "GBP"}

# VIR, VIR SEPA, VIR INST, VIR INST WERO, VIREMENT SEPA RECU, VIREMENT EMIS, VIR SEPA RECU ... (H3)
VIR_PREFIX_RE = re.compile(r"^VIR(?:EMENT)?\b(?:\s+(?:SEPA|INST(?:ANTANE)?|WERO|RECUS?|EMISE?|PERMANENT|"
                           r"OCCASIONNEL|SCT|SDD)\b)*\s*", re.I)


# ---------------------------------------------------------------- structures

@dataclass
class RawTx:
    """What a parser gets: the stored transaction plus the account it belongs to."""
    description: str
    amount: float
    booking_date: str
    currency: str = "EUR"
    counterparty: str = ""
    bank_tx_code: str = ""
    account_type: str = ""
    bank: str = ""
    raw: dict | None = None


@dataclass
class Household:
    """Who the account holders are. `holders` (>= 2 name tokens each) identify OWN-account transfers; `family`
    (every token, single ones included) only flags a probable family member; `first_names` are given names of the
    household, used by the person-vs-company heuristic."""
    holders: list[set[str]] = field(default_factory=list)
    family: set[str] = field(default_factory=set)
    first_names: set[str] = field(default_factory=set)
    own_banks: dict[str, str] = field(default_factory=dict)     # distinctive token of a connected bank -> bank name


PARSED_KEYS = ("tx_type", "op_date", "merchant_raw", "merchant_key", "fx_amount", "fx_currency",
               "counterparty", "mandate_ref", "creditor_id", "reference")


def result(tx_type: str, merchant_raw: str, op_date: str | None = None, fx: tuple | None = None, **extra) -> dict:
    r = {"tx_type": tx_type, "op_date": op_date, "merchant_raw": merchant_raw,
         "fx_amount": fx[0] if fx else None, "fx_currency": fx[1] if fx else None,
         "counterparty": None, "mandate_ref": None, "creditor_id": None, "reference": None}
    r.update(extra)
    r["merchant_key"] = merchant_key(r["merchant_raw"])
    return r


# ---------------------------------------------------------------- text helpers

def strip_accents(text: str) -> str:
    t = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in t if not unicodedata.combining(ch))


def collapse(text: str) -> str:
    return " ".join((text or "").split())


def segments(desc: str, padded: bool = False) -> list[str]:
    """Descriptor fields: the remittance lines joined by ' | ' (CE, CIC, Revolut), or also padded with 2+ spaces
    (Fortuneo: `padded=True`; elsewhere double spaces are just formatting inside a name)."""
    out = []
    for part in re.split(r"\s+\|\s+", (desc or "").strip()):
        out += [p for p in (re.split(r"\s{2,}", part.strip()) if padded else [part.strip()]) if p]
    return out or [""]


def ddmm_date(ddmm: str, booking_date: str, sep: str = "/") -> str | None:
    """'dd/mm' (no year) of a card payment -> ISO date: the latest such date not after the booking date."""
    try:
        d, mth = (int(x) for x in ddmm.split(sep)[:2])
        y = int(booking_date[:4]) - (1 if mth > int(booking_date[5:7]) else 0)
        date(y, mth, d)
        return f"{y}-{mth:02d}-{d:02d}"
    except (ValueError, IndexError):
        return None


def merchant_key(raw: str) -> str:
    out = []
    for tok in (raw or "").upper().split():
        if "*" in tok:
            left, right = tok.split("*", 1)
            if left in PROCESSORS:
                left = ""
            tok = left + (" " + right if right and not re.search(r"\d", right) else "")
        tok = re.sub(r"\d+", "", tok)
        tok = re.sub(r"[^\w' ]+", " ", tok).strip(" '")
        out += [t for t in tok.split() if len(t) >= 2]
    return " ".join(out)


# ---------------------------------------------------------------- holders / household

def _name_tokens(name: str | None) -> set[str]:
    name = PERSON_RE.sub("", (name or "").upper())
    return {t for t in re.split(r"\W+", strip_accents(name)) if len(t) > 2 and t not in TITLES}


def _holder_name(name: str | None, cash_type: str | None) -> bool:
    """Does an account name look like a holder (person) rather than a product label?"""
    up = (name or "").upper().strip()
    if not up or (cash_type or "").upper() == "CARD":
        return False
    toks = re.split(r"\W+", up)
    if any(t in PRODUCT_WORDS for t in toks):
        return False
    return bool(PERSON_RE.match(up + " ")) or 2 <= len([t for t in toks if t]) <= 4


def _household_yaml(p) -> dict:
    """household.yaml as a dict; anything unreadable or malformed (permissions, YAML error, not a mapping) is an empty
    household: a bad hand edit must never stop sync / normalize."""
    import yaml
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:                                    # noqa: BLE001
        return {}
    return data if isinstance(data, dict) else {}


def load_members(memory_dir) -> list[str]:
    """Names of the household members from memory/household.yaml (E14-1) when that file exists:
    ``members: [{id, name, aliases: []}]``. Never required."""
    if not memory_dir:
        return []
    p = Path(memory_dir) / "household.yaml"
    if not p.exists():
        return []
    data = _household_yaml(p)
    names = []
    members = data.get("members", [])
    for m in members if isinstance(members, list) else []:
        if isinstance(m, dict):
            names += [str(m[k]) for k in ("name", "full_name") if m.get(k)]
    return names


def load_member_aliases(memory_dir) -> list[str]:
    """Holder-name spellings of the members (``aliases:`` in household.yaml, E14-1): extra ways a member's name is
    written in bank data ("M OU MME SURNAME GIVEN"). They help recognise transfers to the household's own accounts;
    they are never used as given names."""
    if not memory_dir:
        return []
    p = Path(memory_dir) / "household.yaml"
    if not p.exists():
        return []
    data = _household_yaml(p)
    out = []
    members = data.get("members", [])
    for m in members if isinstance(members, list) else []:
        if isinstance(m, dict) and isinstance(m.get("aliases"), list):
            out += [str(a) for a in m["aliases"] if a]
    return out


def account_holder_tokens(name: str | None, owner: str | None, cash_type: str | None) -> set[str]:
    """Name tokens of the holder of ONE account (possibly a single token; empty when it is not a person)."""
    if owner and owner.strip().lower() != "joint":
        toks = _name_tokens(owner)
        if len(toks) < 2 and _holder_name(name, cash_type):
            nt = _name_tokens(name)
            if toks and toks <= nt:
                toks = nt
        return toks
    return _name_tokens(name) if _holder_name(name, cash_type) else set()


def candidate_holders(con, members: list[str] | None = None) -> list[set[str]]:
    """Name-token set of every account holder / household member, ANY size. `accounts.owner` (a member name or a
    member id such as 'anna', not 'joint') wins; an owner id that is only a first name is completed by the account
    name when that contains it ('Anna Rossi'). Otherwise the account name counts only if it looks like a person."""
    out: list[set[str]] = []

    def add(toks):
        if toks and toks not in out:
            out.append(toks)

    for name, owner, cash_type in con.execute("SELECT name, owner, cash_account_type FROM accounts"):
        add(account_holder_tokens(name, owner, cash_type))
    for m in members or []:
        add(_name_tokens(m))
    return out


def holder_sets(con, members: list[str] | None = None) -> list[set[str]]:
    """Holders usable to recognise a transfer to the user's OWN account: at least 2 name tokens. A single token
    (e.g. {SURNAME} from 'MME J SURNAME ET M M SURNAME', initials dropped) would make every payment to anyone with
    that surname look internal (H2); such sets only serve family detection (:func:`owner_tokens`)."""
    return [h for h in candidate_holders(con, members) if len(h) >= 2]


def owner_tokens(con, members: list[str] | None = None) -> set[str]:
    """Union of the holders' name tokens (family-member detection: shared surname), single-token sets included."""
    toks: set[str] = set()
    for h in candidate_holders(con, members):
        toks |= h
    return toks


def household(con, memory_dir=None) -> Household:
    members = load_members(memory_dir)
    cands = candidate_holders(con, members + load_member_aliases(memory_dir))
    first = set()
    for name, owner in con.execute("SELECT name, owner FROM accounts"):
        for n in (name, owner):
            toks = [t for t in re.split(r"\W+", strip_accents(PERSON_RE.sub("", (n or "").upper()))) if len(t) > 2]
            if toks and _holder_name(n, None):
                first.add(toks[0])
    for m in members:
        toks = [t for t in re.split(r"\W+", strip_accents(m.upper())) if t]
        if toks:
            first.add(toks[0])
    banks = {}
    for (b,) in con.execute("SELECT DISTINCT bank FROM accounts WHERE bank IS NOT NULL"):
        if t := bank_token(b):
            banks[t] = b
    return Household(holders=[h for h in cands if len(h) >= 2], family=set().union(*cands) if cands else set(),
                     first_names=first, own_banks=banks)


GENERIC_BANK_WORDS = {"CAISSE", "BANQUE", "BANK", "BANCA", "CREDIT", "EPARGNE", "HAUTS", "FRANCE", "ITALIA",
                      "GROUPE", "POPULAIRE", "AGRICOLE", "MUTUEL", "LA", "LE", "DE", "DES", "DU", "ET"}


def bank_token(bank: str | None) -> str | None:
    """The distinctive word of a bank's name ('Fortuneo', 'CIC', 'Revolut'); None for names made of generic words."""
    for t in re.split(r"\W+", strip_accents(bank or "").upper()):
        if len(t) >= 3 and t not in GENERIC_BANK_WORDS:
            return t
    return None


# ---------------------------------------------------------------- transfer counterparties

_TAGS = r"MOTIF|MTF|OBJET|REF|REFERENCE|RI|DE|LIB|LIBELLE|EREF|RUM|ICS|ID|INFO|ORDRE|DATE|BIC|IBAN|NOTPROVIDED|NOM|BEN|ORD"
TAG_CUT_RE = re.compile(rf"\s*/(?:{_TAGS})\b:?.*$", re.I)           # with or without a leading space, '/DE:' form
# only these tags carry the COUNTERPARTY; every other tag (/MOTIF, /OBJET, /LIB, /REF...) is a free-text reason
COUNTERPARTY_TAGS = {"DE", "A", "BEN", "ORD", "NOM"}
_FIELD_RE = re.compile(rf"/({_TAGS}|A)\b:?\s*", re.I)
# reasons written WITHOUT a slash: "ACME SARL MOTIF: SEANCE", "ACME SARL MOTIF THERAPIE", "... REF: 123", "... RI: 9"
BARE_REASON_RE = re.compile(r"\s+(?:(?:MOTIF|MTF|OBJET|LIBELLE)\b|(?:LIB|REF|REFERENCE|RI|EREF|INFO)\b\s*:).*$", re.I)


def counterparty_from_tags(t: str) -> str | None:
    """For text that starts with SEPA field tags ('/DE ACME /MOTIF x'): the counterparty ('' if the tags give none).
    None when the text is not tag-structured."""
    if not t.startswith("/"):
        return None
    parts = _FIELD_RE.split(t)                 # ['', TAG, value, TAG, value...]
    for tag, val in zip(parts[1::2], parts[2::2]):
        if tag.upper() in COUNTERPARTY_TAGS:
            return val.strip()
    return ""


def strip_reason(t: str) -> str:
    """Counterparty text without any reason: tag-structured, slashed or bare 'MOTIF ...' tails are dropped."""
    tagged = counterparty_from_tags(t)
    if tagged is not None:
        return tagged
    return BARE_REASON_RE.sub("", TAG_CUT_RE.sub("", t)).strip()


def strip_vir_prefix(text: str) -> str:
    """'VIR SEPA RECU X' / 'VIREMENT INST X' / 'VIR INST WERO X' -> 'X'. SEPA field tags: in
    'VIR SEPA RECU /DE <payer> /MOTIF <reason> /REF <ref>' only the counterparty tags (/DE, /A, /BEN, /ORD, /NOM)
    define the party; a reason tag (/MOTIF, /OBJET, /LIB, /REF...) NEVER does, and a line with tags but no
    counterparty gives an empty key (such an item is not sent anywhere). Free text never enters a key (P4)."""
    t = VIR_PREFIX_RE.sub("", (text or "").strip(), count=1)
    return strip_reason(t)


def lead_tokens(head: str) -> list[str]:
    """Words of the counterparty name at the START of a transfer descriptor, titles and SEPA/INST dropped."""
    toks = [t for t in re.split(r"\W+", strip_accents(head).upper()) if t]
    while toks and (toks[0] in TITLES or toks[0] in PREFIX_WORDS):
        toks.pop(0)
    return toks


def is_own_name(head: str, holders: list[set[str]]) -> bool:
    """The transfer's counterparty IS one of the holders: that holder's tokens are all among the leading words
    (not anywhere in the text: a salary line may end with the employee's name)."""
    lead = lead_tokens(head)
    return any(len(h) >= 2 and h <= set(lead[: len(h) + 1]) for h in holders if h)


def has_reference_token(tokens: list[str]) -> bool:
    """A word mixing letters and digits ('FAC20310042'): an invoice / mandate reference, not part of a name.
    Pure digit groups (an account number written before a name) do not count."""
    return any(re.search(r"\d", t) and re.search(r"[A-Z]", t) for t in tokens)


def family_in_lead(head: str, family: set[str]) -> bool:
    """A surname shared with the household in the first words of the counterparty. A token with digits
    (an invoice number, 'FAC20310042 SURNAME') means the text is not a plain personal name."""
    lead = lead_tokens(head)[:4]
    return bool(family & set(lead)) and not has_reference_token(lead)


FIRST_NAMES = set("""
ADRIEN ALAIN ALBERT ALEXANDRE ALEXIS ALICE ALINE ALISON AMANDINE AMELIE ANAIS ANDRE ANGELA ANGELIQUE ANNA ANNE
ANTOINE ANTOINETTE ANTONIO ARNAUD ARTHUR AUDREY AURELIE AURELIEN BAPTISTE BARBARA BEATRICE BENJAMIN
BENOIT BERNARD BERTRAND BRUNO CAROLINE CATHERINE CECILE CEDRIC CELINE CHARLES CHARLOTTE CHLOE CHRISTELLE
CHRISTIAN CHRISTINE CHRISTOPHE CLAIRE CLARA CLAUDE CLEMENCE CLEMENT COLETTE CORENTIN DAMIEN DANIEL DANIELE
DAVID DELPHINE DENIS DIDIER DOMINIQUE EDOUARD ELISE ELODIE EMILIE EMILIEN EMMA EMMANUEL EMMANUELLE ERIC ESTELLE
ETIENNE EVA EVE FABIEN FABRICE FANNY FLORENCE FLORENT FLORIAN FRANCIS FRANCOIS FRANCOISE FREDERIC FREDERIQUE
GABRIEL GABRIELLE GAEL GAELLE GAUTIER GEORGES GERARD GHISLAINE GILLES GREGORY GUILLAUME GUY HELENE HENRI
HERVE HUGO ISABELLE JACQUES JEAN JEANNE JEREMY JEROME JESSICA JOEL JONATHAN JORDAN JOSEPH JOSEPHINE JULES JULIA
JULIEN JUSTINE KARINE KEVIN LAETITIA LAURA LAURE LAURENCE LAURENT LEA LEO LEON LILIAN LOIC LOUIS LOUISE
LUC LUCAS LUCIE LUDOVIC MAEL MAGALI MANON MARC MARCEL MARGAUX MARIE MARIO MARION MARTIN MATHIEU MATHILDE MATTHIEU
MAXIME MELANIE MICHEL MICHELE MORGANE MYRIAM NADEGE NADINE NATHALIE NATHAN NICOLAS NOEMIE OCEANE OLIVIA OLIVIER
PASCAL PASCALE PATRICE PATRICIA PATRICK PAUL PAULINE PHILIPPE PIERRE QUENTIN RAPHAEL REGIS REMI REMY RENE
RICHARD ROBERT ROMAIN ROMANE ROSE SABINE SAMUEL SANDRA SANDRINE SARAH SEBASTIEN SERGE SIMON SOLENE SONIA SOPHIE
STEPHANE STEPHANIE SUZANNE SYLVAIN SYLVIE TANGUY THEO THERESE THIBAULT THIBAUT THIERRY THOMAS TIMOTHEE TRISTAN
ULYSSE VALENTIN VALERIE VANESSA VERONIQUE VICTOR VICTORIA VINCENT VIRGINIE XAVIER YANN YVES ZOE
ALESSANDRO ALESSIA ALBERTO ANDREA ANGELO ANTONELLA BEATRICE CARLO CHIARA CLAUDIO DANIELA DAVIDE DOMENICO
ELENA ELEONORA ENRICO FABIO FEDERICA FEDERICO FILIPPO FRANCESCA FRANCESCO GIACOMO GIORGIA GIORGIO GIOVANNI
GIULIA GIUSEPPE GIUSEPPINA GIANLUCA LORENZO LUCA LUCIA LUIGI MARIA MASSIMO MATTEO MATTIA MAURIZIO MICHELA
NICOLA PAOLA PAOLO PIETRO RICCARDO ROBERTA ROBERTO ROSA SALVATORE SARA SERGIO SILVIA SIMONE STEFANIA STEFANO
TOMMASO VALENTINA VINCENZO VITTORIO
ADAM ALI AMINE AMIRA ANTHONY AYMAN BASSAM FATIMA GIULIO GIULIANO HASSAN HOA IBRAHIM KOFFI LAILA LINA MARIAM MEHDI MOHAMED MOHAMMED NADIA OMAR RACHID SAMIR SAMIRA SARRA SOFIA YASMINE YOUSSEF YACINE YUSUF
ALEX BEN CHRIS DANIEL DAVID EMILY GEORGE HARRY JACK JAMES JANE JOHN JOSHUA LUKE MARK MARY MICHAEL OLIVER PETER
RACHEL SAM SOPHIA STEVE TOM WILLIAM
""".split())

ORG_WORDS = set("""
SAS SARL SA SASU SCI SNC EURL SRL SPA SPAS SCOP COOP ASSOC ASSOCIATION SYNDICAT SYNDIC CAISSE BANQUE BANK CREDIT
MUTUELLE ASSURANCE ASSURANCES ECOLE COLLEGE LYCEE UNIVERSITE CLUB MAIRIE COMMUNE TRESOR DGFIP DRFIP FINANCES
PUBLIQUES PUBLIC SERVICE SERVICES GROUPE GROUP COMPAGNIE SOCIETE ENTREPRISE ETS ETABLISSEMENTS AGENCE CABINET
CLINIQUE HOPITAL CENTRE CENTER MAISON GARAGE BOULANGERIE PHARMACIE RESTAURANT HOTEL SOCIETA COMUNE ASL INPS
AGENZIA CONDOMINIO STUDIO FRANCE EUROPE PAYMENTS PAYMENT LTD LIMITED INC GMBH BV NV TELECOM ENERGIE ENERGY EDF
ENGIE ORANGE FREE SFR BOUYGUES AMAZON PAYPAL REVOLUT LEROY MERLIN CARREFOUR AUCHAN LECLERC INTERMARCHE LIDL
CITYA NEXITY URSSAF CPAM CAF MSA SECU SECURITE SOCIALE ASSURANCES MUTUELLES GMBH SOFTWARE HOLDING
FONDATION FEDERATION UNION SYNDICATS REGIE AUTOROUTES MOBILITE LOCATION LEASING ELECTRICITE EAU GAZ GAS
INTERNET MOBILE TELEPHONE POSTE IMPOTS TAXE TAXES AMENDE LOYER IARD VIE
""".split())
CONNECTORS = {"DE", "DU", "DES", "LA", "LE", "LES", "ET", "AU", "AUX", "SUR", "EN", "DI", "DEL", "DELLA", "DEI",
              "DEGLI", "OF", "AND", "THE", "D", "L"}
PROF_TITLE_RE = re.compile(r"^(DR|DOCTEUR|DOTT|DOTTORE|MAITRE|ME|PR|PROF|PROFESSEUR)\b\.?\s", re.I)


def looks_like_person(text: str, first_names: set[str] | None = None) -> bool:
    """Conservative guess that a counterparty is a natural person: 2-4 alphabetic words without any legal form,
    organisation word, connector (DE/DU/ET...) or digit, and either a title, an initial ('PAUL M'), or a known given
    name. Used so that person names never reach an LLM; anything unsure is treated as NOT a person here and is
    caught by the other guards."""
    t = strip_accents(text or "").upper().strip()
    if not t or re.search(r"\d", t):
        return False
    if PERSON_RE.match(t + " ") or PROF_TITLE_RE.match(t + " "):
        return True
    toks = [x for x in re.split(r"[^A-Z'-]+", t) if x]
    toks = [x for x in toks if x not in PREFIX_WORDS]
    if not 2 <= len(toks) <= 4:
        return False
    if any(x in ORG_WORDS or x in CONNECTORS for x in toks):
        return False
    names = FIRST_NAMES | (first_names or set())
    return any(len(x) == 1 for x in toks) or any(x.replace("-", " ").split()[0] in names for x in toks)


def names_other_own_bank(head: str, h: Household, own_bank: str | None) -> bool:
    """'FORTUNEO', 'CIC <household first names>': the counterparty is the label of the user's account at ANOTHER
    connected bank, not a third party."""
    lead = lead_tokens(head)
    if not lead or lead[0] not in h.own_banks or lead[0] == bank_token(own_bank):
        return False
    return len(lead) == 1 or bool(set(lead[1:]) & (h.family | h.first_names))


def classify_transfer(head: str, amount: float, h: Household, wero: bool = False, smart: bool = True,
                      own_bank: str | None = None) -> str:
    """tx_type of a transfer from its counterparty text (prefix already stripped)."""
    if is_own_name(head, h.holders) or re.search(r"Virement avec|Livret", head, re.I) or (
            smart and names_other_own_bank(head, h, own_bank)):
        return "internal_transfer"
    if wero:
        return "wero_in" if amount > 0 else "wero_out"
    if PERSON_RE.match(head + " ") or family_in_lead(head, h.family) or (
            smart and (looks_like_person(head, h.first_names) or lead_is_p2p(head))):
        return "person_transfer_in" if amount > 0 else "person_transfer_out"
    return "transfer_in" if amount > 0 else "transfer_out"


P2P_PREFIXES = {"PAYLIB", "LYDIA", "SATISPAY", "WERO"}


def lead_is_p2p(head: str) -> bool:
    toks = lead_tokens(head)
    return bool(toks) and toks[0] in P2P_PREFIXES
