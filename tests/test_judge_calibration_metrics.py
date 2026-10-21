from scripts.report_hard_benchmark import score_metrics


def row(human, model, question='same'):
    return {'question_id':question,'human':{'overall_score':human},'model':{'overall_score':model}}


def test_matching_histogram_is_not_matching_human_ranking():
    result=score_metrics([row(2,4),row(4,2)],'overall_score')
    assert result['distribution_wasserstein']==0
    assert result['mae']==2
    assert result['pairwise_order_agreement']==0


def test_ties_and_missing_scores_do_not_become_perfect_agreement():
    result=score_metrics([row(3,4),row(3,3),row(None,3)],'overall_score')
    assert result['n']==2
    assert result['same_question_unequal_pairs']==0
    assert result['pairwise_order_agreement'] is None
    assert result['tie_agreement']==0
    assert score_metrics([],'overall_score')=={'n':0}
