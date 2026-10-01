"""Pure, payload-private promotion proof; caller supplies read-only snapshots."""


def verify_delivery(*, manifest, native_status, native_records, receipts, inbox, owner_id, scope):
    counters=('pending','quarantined','conflicts','active_workers','shutdown_publications','foreign','foreign_retained','delivered')
    try:
        count=manifest['expected_count']
        if (manifest['complete'] is not True or type(count) is not int or count<=0
                or type(manifest['captured_count']) is not int or manifest['captured_count']!=count
                or len(manifest['records'])!=count
                or any(type(native_status[k]) is not int or native_status[k]<0 for k in counters)
                or any(native_status[k] for k in counters[:5])
                or native_status['foreign']!=native_status['foreign_retained']
                or native_status['delivered']<count):
            raise ValueError('Incomplete delivery evidence')
    except (KeyError,TypeError):
        raise ValueError('Malformed delivery evidence') from None
    import hashlib,json
    from backend.background_delivery import BackgroundDeliveryService
    def digest(value):
        return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),
                            ensure_ascii=False,allow_nan=False).encode()).hexdigest()
    def one(rows,key,value):
        matches=[row for row in rows if row[key]==value]
        if len(matches)!=1:raise ValueError('Missing or ambiguous delivery record')
        return matches[0]
    try:
        if not isinstance(owner_id,str) or not owner_id or not isinstance(scope,str) or not scope:
            raise ValueError()
        seen=set()
        for entry in manifest['records']:
            key=entry['event_id']
            if not isinstance(key,str) or key in seen:raise ValueError()
            seen.add(key)
            record=one(native_records,'event_id',key)
            receipt=one(receipts,'event_id',key)
            message=one(inbox,'id',receipt['inbox_id'])
            event=json.loads(record['event_json']);result=json.loads(record['result_json'])
            public_event=json.loads(receipt['event_json'])
            if not isinstance(event,dict) or not isinstance(result,dict) or not isinstance(public_event,dict):
                raise ValueError()
            strip=lambda value:{k:v for k,v in value.items() if k!='restored'}
            if (key!='async:'+event['delegation_id'] or event['type']!='async_delegation'
                    or digest(strip(event))!=entry['payload_sha256']
                    or digest(strip(public_event))!=entry['payload_sha256']
                    or digest(result)!=entry['result_sha256']
                    or record['payload_sha256']!=entry['payload_sha256']
                    or record['state']!='delivered' or record['route']!='owned'
                    or type(record['historical']) is not int or record['historical']!=1
                    or record['receipt_id']!=receipt['inbox_id']
                    or receipt['scope']!=scope or receipt['user_id']!=owner_id
                    or receipt['digest']!=entry['payload_sha256']
                    or receipt['origin']!=event['origin_session_id']
                    or type(receipt['acknowledged']) is not int or receipt['acknowledged']!=1
                    or message['user_id']!=owner_id or message['title']!='Background result'
                    or message['body']!=BackgroundDeliveryService._body(event)):
                raise ValueError()
    except (KeyError,TypeError,ValueError):
        raise ValueError('Delivery chain incomplete or inconsistent') from None
    return {'status':'delivered','verified_count':count}
