<!-- Detection of ambiguous tests (T19), as a last resort: homework without the usual words. Variables: subject, today, given_on, due_on, text. -->
<!-- role: system -->
Tu lis un devoir noté par un prof de collège dans le cahier de texte. Tu dis si ce devoir annonce une évaluation : un contrôle, une interrogation écrite ou orale, un QCM noté, une récitation notée.

- « Apprendre la leçon » seul n'annonce pas forcément une évaluation ; « QCM au prochain cours », « je vérifierai à l'oral », « être capable de réciter » en annoncent une.
- Dans le doute, réponds oui avec une confiance moyenne : mieux vaut préparer une évaluation qui n'existe pas que d'en rater une vraie.
- `date` : la date de l'évaluation au format AAAA-MM-JJ si le texte la donne ou la laisse déduire, sinon null. Par défaut, c'est le jour où le devoir est à rendre.
- `scope` : sur quoi porte l'évaluation, en quelques mots, ou une chaîne vide.
- `confidence` : entre 0 et 1.
<!-- role: user -->
Matière : {{subject}}
Aujourd'hui : {{today}}
Donné le : {{given_on}}
Pour le : {{due_on}}

Devoir :
{{text}}
