"""How much of what a reference found did the flow recover: the misses that evidence and quote checks cannot see."""


def completeness(references, predictions, *, is_positive, matches=None, abstentions=(None,)):
    """Count reference positives recovered, answered differently, abstained on or never assessed.

    references and predictions map case ids to values. A case absent from predictions was not assessed
    (dropped, set aside or never reached). Abstention values (by default None) are counted separately,
    never as agreement. matches(prediction, reference) decides recovery; equality by default.
    """
    matches = matches or (lambda prediction, reference: prediction == reference)
    positives = [case for case, value in references.items() if is_positive(value)]
    counts = {'recovered': 0, 'different': 0, 'abstained': 0, 'missing': 0}
    for case in positives:
        if case not in predictions:
            counts['missing'] += 1
        elif predictions[case] in abstentions:
            counts['abstained'] += 1
        elif matches(predictions[case], references[case]):
            counts['recovered'] += 1
        else:
            counts['different'] += 1
    answered = [case for case in predictions if case in references]
    abstained_all = sum(predictions[case] in abstentions for case in answered)
    n = len(positives)
    return {'reference_positives': n, **counts,
            'recall': counts['recovered'] / n if n else None,
            'abstention_rate': abstained_all / len(answered) if answered else None,
            'positive_abstention_share': counts['abstained'] / n if n else None}
