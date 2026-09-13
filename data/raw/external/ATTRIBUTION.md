# VoteHub / FiveThirtyEight polling attribution
#
# Senate race polls and 2026 generic-ballot polls:
#   Source: VoteHub Polling API (https://api.votehub.com)
#   Docs:   https://votehub.com/polls/api/
#   License: Creative Commons Attribution 4.0 International (CC BY 4.0)
#   Attribution required: "Polling data from VoteHub (https://votehub.com)"
#
# Pollster Scorecards (grades, house effect, error metrics):
#   Source: https://votehub.com/polls/pollster-scorecards/
#   Used as hierarchical priors for house effects and poll quality weights.
#
# Fill-in pollster ratings for firms not on VoteHub Scorecards:
#   FiveThirtyEight pollster-ratings-combined.csv (CC BY 4.0)
#   https://github.com/fivethirtyeight/data/tree/master/pollster-ratings
#
# Raw snapshots live under data/raw/external/ with sha256 manifests in
# data/manifests/. Refresh with:
#   python -m midterms.cli fetch-external
#   python -m midterms.cli ingest-polls
