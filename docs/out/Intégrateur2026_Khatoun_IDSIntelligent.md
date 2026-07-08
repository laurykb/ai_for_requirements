
<!-- page:1 -->


## Projet : Développement d'un détecteur d'intrusion intelligent

## Encadrant : Rida Khatoun

Objectif : Développer  système  de  détection  basé  sur  machine  learning/deep  learning  pour  identifier  des comportements malveillants dans les données CAN d'un camion, en utilisant le dataset collecté sur les dispositifs d'enregistrement électroniques (ELDs). Le dataset a été collecté lors de tests contrôlés sur un camion Kenworth T270 équipé d'un ELD commercialement disponible, durant lesquels le firmware de l'ELD a été remplacé à distance via une connexion Wi-Fi depuis un véhicule passager circulant à proximité. L'ELD compromis a ensuite acquis la capacité d'effectuer des écritures de messages CAN arbitraires selon le choix de l'attaquant.

## Étapes du Projet :

- Explorer le dataset pour identifier les caractéristiques des messages CAN normaux et suspects.

- Prétraiter les données pour éliminer le bruit et normaliser les formats.

- Comparer différents algorithmes de machine learning (comme les arbres de décision, SVM, ou réseaux de neurones) pour choisir celui qui convient le mieux à la classification des messages.

- Diviser le dataset en ensembles d'entraînement et de test.

- Entraîner le modèle sur les comportements normaux et malveillants.

- Évaluer la performance du modèle à l'aide de métriques telles que la précision, le rappel et la courbe ROC.

- Optimiser les hyperparamètres pour améliorer la précision.