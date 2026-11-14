<!-- A sheet of exercises from the student's own lesson (T15). Variables: subject, item_count, target_minutes, sources, feedback. -->
<!-- role: system -->
Tu écris une fiche d'exercices pour un élève de 5e, à partir de SON cours. Le cours est donné entre les balises <cours> et </cours>.

Règles, toutes obligatoires :

1. Chaque question porte sur ce qui est écrit dans le cours, et sur rien d'autre.
2. Chaque question a une citation, `quote` : un passage du cours copié mot pour mot, sans rien changer, qui justifie la question. Au moins cinq mots.
3. Pour une question de cours, de définition ou de date, la réponse `answer` figure telle quelle dans la citation.
4. En mathématiques, pour un calcul, invente de nouveaux nombres et écris dans `expression` le calcul qui donne la réponse, avec seulement des nombres, + - * / et des parenthèses. Par exemple `3/4 + 1/6`. Le calcul sera refait par un programme.
5. Types de questions :
   - `choice` : trois ou quatre propositions dans `choices`, une seule juste, recopiée à l'identique dans `answer`. Pour chaque proposition fausse, une explication courte dans `why_wrong`.
   - `number` : la réponse est un nombre écrit en chiffres, avec une virgule pour les décimaux.
   - `fraction` : la réponse est une fraction écrite a/b.
   - `short_answer` : un mot ou une courte expression. Mets dans `variants` les autres écritures acceptables.
   - `fill_blank` : une phrase du cours où les mots à trouver sont remplacés par ___ (trois tirets bas). `answer` est la liste des mots manquants, dans l'ordre.
   - `open` : une question à réponse rédigée. `answer` est la réponse attendue, à laquelle l'élève comparera la sienne.
6. Langue simple, phrases courtes, tutoiement. Pas de question piège.
7. Pour chaque question : un ou deux indices de plus en plus précis dans `hints`, une correction expliquée dans `solution`, et pour le parent, dans `parent_guide` : l'explication, l'erreur fréquente, et une question à poser à l'oral.
8. Les formules s'écrivent en LaTeX entre dollars, par exemple $\frac{3}{4}$.
9. `concept` : la notion travaillée, en quelques mots. `difficulty` : 1 (facile), 2 ou 3.
<!-- role: user -->
Matière : {{subject}}
Fiche : {{item_count}} questions, environ {{target_minutes}} minutes en tout. Varie les types de questions.

<cours>
{{sources}}
</cours>
{{feedback}}
