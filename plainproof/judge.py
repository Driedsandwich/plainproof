"""Only visible page text determines check results, never the operator's claim."""


def claim_verdict(claim):
    text = (claim or '').lower()
    yes = any(s in text for s in ('still listed', 'still present', 'persisted', 'survived', 'counter is correct'))
    no = any(s in text for s in ('disappeared', 'not listed', 'not present', 'did not persist',
                                'not still listed', 'not still present', 'not persisted',
                                'no longer listed', 'counter is incorrect'))
    return ('PASS' if yes else 'FAIL') if yes != no else None


def evaluate(checks, checkpoints, stop):
    results = []
    missing = False
    for check in checks:
        targets = [p for p in checkpoints if
                   (p['source'] in {'model', 'audit'} if check['when'] == 'after_each_reload'
                    else p['source'] in {'end', 'audit'})]
        # An audit alone does not prove the operator followed a persistence test.
        covered = bool(targets) and (check['when'] != 'after_each_reload' or
                                    any(p['source'] == 'model' for p in targets))
        missing |= not covered
        if not targets:
            results.append({'check': check, 'passed': None, 'evidence': 'No matching checkpoint',
                            'checkpoint': None, 'covered': False})
        for cp in targets:
            text, needle = cp['page_text'], check['text']
            count = text.count(needle)
            passed = (count > 0 if check['kind'] == 'text_visible' else
                      count == 0 if check['kind'] == 'text_absent' else count == check['count'])
            at = text.find(needle)
            snippet = text[max(0, at - 80):at + len(needle) + 80] if at >= 0 else text[:240]
            results.append({'check': check, 'checkpoint': cp['id'], 'source': cp['source'],
                            'passed': passed, 'actual_count': count, 'evidence': snippet,
                            'covered': covered, 'screenshot': cp['screenshot']})
    if stop == 'safety_check':
        verdict = 'stopped_safety_check'
    elif stop == 'error':
        verdict = 'ERROR'
    elif stop != 'model_finished' or missing:
        verdict = 'INCONCLUSIVE'
    else:
        verdict = 'FAIL' if any(r['passed'] is False for r in results) else 'PASS'
    return verdict, results
