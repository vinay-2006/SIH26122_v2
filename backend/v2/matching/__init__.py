"""Automatic activity matching for v2 claims.

This package is an ADAPTER around the existing, unchanged matching engine (backend/routers/matching.py: match_claim and the four tiers, the scoring and
the semantic retrieval text/model of backend/shared/schedule_index.py). It translates v2 data into the shapes the engine already understands, calls the
engine, and writes the result into the v2 tables. It contains no scoring, weighting, threshold-tuning or ranking logic of its own.
"""
