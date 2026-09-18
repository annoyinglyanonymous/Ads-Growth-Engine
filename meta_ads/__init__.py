"""Read the ads back: what ran in the account, and what it did.

    python -m meta_ads --pull --brand renegade

The half of measurement tracking.py's docstring says was left for later. Every
approved asset already ships with its slot in utm_content ('ad-a-v5'); this is
what reads those names back, alongside every ad in the account that this engine
did not write, which is the set the review stage exists for.

THE SPLIT, AND WHY IT IS WHERE IT IS
`parse` is pure: given a blob of Graph JSON it works out the texts, the links,
the utm values and one insights row, and it touches no database, no network and
no settings. `client` is the only thing that talks to Meta. Everything above
them (`store`, `pull`) is a caller of both. The split is not tidiness: a
creative shape is the part most likely to be parsed wrong, and a parser that
needed an account and a token to test would be a parser tested on one shape.

NOTHING HERE WRITES TO AN AD ACCOUNT, AND NOTHING HERE CALLS A MODEL. The token
carries ads_read; the judgement about what the copy is worth belongs to a
skill, reading rows this package imported.
"""
