"""Russian names of popular cities mapped to Latin spellings.

Used by the timezone resolver so users can type «москва» or «новосибирск»
instead of an IANA zone name.
"""

TRANSLITERATION: dict[str, str] = {
    "москва": "moscow",
    "мск": "moscow",
    "спб": "saint_petersburg",
    "санкт-петербург": "saint_petersburg",
    "питер": "saint_petersburg",
    "калининград": "kaliningrad",
    "самара": "samara",
    "екатеринбург": "yekaterinburg",
    "екб": "yekaterinburg",
    "омск": "omsk",
    "новосибирск": "novosibirsk",
    "красноярск": "krasnoyarsk",
    "иркутск": "irkutsk",
    "якутск": "yakutsk",
    "владивосток": "vladivostok",
    "магадан": "magadan",
    "петропавловск-камчатский": "kamchatka",
    "камчатка": "kamchatka",
    "киев": "kyiv",
    "минск": "minsk",
    "рига": "riga",
    "алматы": "almaty",
    "астана": "astana",
    "лондон": "london",
    "берлин": "berlin",
    "нью-йорк": "new_york",
    "тбилиси": "tbilisi",
    "ташкент": "tashkent",
    "тель-авив": "tel_aviv",
    "дубай": "dubai",
}
