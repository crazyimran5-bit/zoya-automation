"""Only documented Skales SSE events count as tool execution evidence."""
def classify_events(events, required_tools=()):
    calls = {e.get('tool') for e in events if e.get('type') == 'tool_call' and e.get('tool')}
    results = {e.get('tool') for e in events if e.get('type') == 'tool_result'
               and e.get('tool') and isinstance(e.get('result'), str)
               and e['result'].strip()}
    failures = {e.get('tool') for e in events if e.get('type') == 'tool_error'}
    # tool_result is a truncated display string, not full business acceptance evidence.
    observed = sorted((calls & results) - failures)
    done = any(e.get('type') == 'done' for e in events)
    error = any(e.get('type') in ('error', 'tool_error') for e in events)
    fallback = any('produced without tools' in str(e.get('content', '')).lower()
                   for e in events if e.get('type') == 'text')
    required = set(required_tools)
    reason = ('provider_error' if error else 'local_model_tool_timeout' if fallback
              else 'missing_required_tool_result' if required - set(observed)
              else 'no_tool_execution' if not observed else 'incomplete_stream' if not done
              else 'tool_events_observed')
    good = done and not error and not fallback and bool(observed) and not (required - set(observed))
    return {'status': 'tool_result_received' if good else 'needs_review',
            'reason': reason, 'observed_tools': observed,
            'tool_execution_observed': good,
            'tool_execution_verified': False,
            'business_outcome_verified': False}
