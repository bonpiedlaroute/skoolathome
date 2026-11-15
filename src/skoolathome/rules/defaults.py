"""The default behaviour, written as rules like any other (ARCHITECTURE.md §7.2)."""

from __future__ import annotations

from skoolathome.schema.rule import Origin, Rule

DEFAULT_RULES = (
    Rule(
        id="daily", trigger="after_each_lesson", scope="this_lesson", format="exercises", target_minutes=8,
        origin=Origin(text="Après chaque cours qui a du contenu exploitable, un exercice court sur la séance."),
    ),
    Rule(
        id="before_test", trigger="before_test", scope="since_last_test", format="test_revision",
        offsets_days=[4, 2, 1], target_minutes=10,
        origin=Origin(text="Avant un contrôle, une révision étalée : quatre jours avant, rappel de cours et "
                           "exercices de base ; deux jours avant, exercices mélangés ; la veille, un petit "
                           "contrôle d'entraînement."),
    ),
    Rule(
        id="missed_concepts", trigger="after_feedback", scope="missed_items", format="test_revision",
        target_minutes=5,
        origin=Origin(text="Les notions ratées reviennent le lendemain."),
    ),
    Rule(
        id="max_evening_load", trigger="load_limit", max_minutes=20,
        origin=Origin(text="20 minutes au total par soir, toutes matières confondues."),
    ),
)
