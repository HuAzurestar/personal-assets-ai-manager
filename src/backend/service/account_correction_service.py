"""Derive account-only immutable copies; never accept client financial outputs.

The caller owns the read snapshot or short write slot. Active source consumers
are copied as complete groups too, so their OUT legs point to the new IN rather
than silently becoming NEEDS_REVIEW. Old groups and source IDs remain readable.
"""
from collections import defaultdict

from backend.service.review_intent_service import reject


ECONOMIC_NAMES = {0:'TRANSACTION',1:'ACCOUNT_TRANSFER',2:'ASSET_LIABILITY',3:'DUPLICATE'}


def account_correction_drafts(mapper, corrections, duplicate_decisions):
    if not corrections:
        return [], set()
    overrides={row.ledger_id:row.account_ref_id for row in corrections}
    ledgers={row['id']:row for row in mapper.named_rows('ledgers',overrides)}
    allocations=mapper.allocations_for_ledgers(overrides)
    if set(ledgers)!=set(overrides) or {row['ledger_id'] for row in allocations}!=set(overrides):
        reject('LEDGER_NOT_FOUND','所选流水或其完整解释关系不存在',status=404)
    if any(ledgers[lid]['account_ref_id']==ref for lid,ref in overrides.items()):
        reject('ACCOUNT_CORRECTION_NO_CHANGE','来源未改变；请移除无需更正的流水')
    review_ids={row['review_id'] for row in allocations}
    bundle=mapper.bundle(review_ids)
    if any(row['status']!=0 for row in bundle['reviews']):
        reject('ACCOUNT_CORRECTION_NOT_ACTIVE','原解释已停用；须重新读取当前流水',status=409)

    # Set-oriented dependency closure, bounded by the unchanged Review budget.
    # No per-Ledger SQL or financial mutation is performed in this traversal.
    frontier={row['id'] for row in bundle['position_legs']}
    while frontier:
        dependents=mapper.dependents(frontier)
        candidates={row['review_id'] for row in dependents}-review_ids
        active={row['id'] for row in mapper.named_rows('reviews',candidates) if row['status']==0}
        if not active:
            break
        if len(review_ids|active)>100:
            reject('REVIEW_CHANGE_LIMIT','账户更正的完整数量依赖组超过100；不能拆开原子依赖',status=413)
        extra=mapper.bundle(active)
        for key in bundle:
            bundle[key].extend(extra[key])
            if key!='reviews' and len(bundle[key])>4000:
                reject('REVIEW_CHANGE_LIMIT','账户更正完整输出或归因超出预算；未部分复制',status=413)
        review_ids.update(active)
        frontier={row['id'] for row in extra['position_legs']}

    reviews={row['id']:row for row in bundle['reviews']}
    flows={row['id']:row for row in bundle['ledger_entries']}
    by_review=defaultdict(list);legs_by_review=defaultdict(list);links_by_review=defaultdict(list)
    for row in bundle['allocations']: by_review[row['review_id']].append(row)
    for row in bundle['position_legs']: legs_by_review[row['review_id']].append(row)
    for row in bundle['position_allocations']: links_by_review[row['review_id']].append(row)
    duplicate_ids={row['transaction_id'] for row in bundle['allocations'] if flows[row['ledger_id']]['entry_type']==3}
    decisions={row.transaction_id:row.model_dump() for row in duplicate_decisions}
    if set(decisions)!=duplicate_ids:
        reject('ACCOUNT_CORRECTION_KEEPER_REQUIRED','整组含重复证据；须逐份明确仍计现金的保留交易，不能推断旧目标')
    copied_sources={row['id'] for row in bundle['position_legs'] if row['leg_direction']=='IN'}
    drafts=[]
    for rid in sorted(review_ids):
        original=reviews[rid]
        splits=sorted(by_review[rid],key=lambda row:row['id'])
        legs=sorted(legs_by_review[rid],key=lambda row:row['id'])
        flow_indexes={row['ledger_id']:index for index,row in enumerate(splits)}
        leg_indexes={row['id']:index for index,row in enumerate(legs)}
        money=[dict(transaction_id=row['transaction_id'],economic_type=ECONOMIC_NAMES[flows[row['ledger_id']]['entry_type']],
            entry_type=flows[row['ledger_id']]['entry_type'],cash_amount=row['cash_amount'],
            original_account_code=flows[row['ledger_id']]['account_code'],
            original_counterparty_account_ref=flows[row['ledger_id']]['counterparty_account_ref'],
            account_ref_id=overrides.get(row['ledger_id'],flows[row['ledger_id']]['account_ref_id'])) for row in splits]
        duplicates={row['transaction_id'] for row in money if row['entry_type']==3}
        bindings=[]
        for fid in sorted(duplicates):
            refs={row['account_ref_id'] for row in money if row['transaction_id']==fid}
            if len(refs)!=1 or not next(iter(refs)):
                reject('INVALID_DUPLICATE','重复证据必须保留唯一已知来源，不可仅改一个拆分行')
            bindings.append(dict(transaction_id=fid,account_ref_id=next(iter(refs))))
        copied_legs=[dict(existing_position_id=row['position_id'],new_position_index=None,type=row['type'],
            leg_amount=row['leg_amount'],leg_direction=row['leg_direction'],occurred_time=row['occurred_time'],
            source=row['source_position_leg_id'],basis=row['basis'],copied_leg_id=row['id'],
            **({'replacement_source_leg_id':row['source_position_leg_id']}
                if row['source_position_leg_id'] in copied_sources else {})) for row in legs]
        links=[dict(allocation_index=flow_indexes[row['ledger_id']],leg_index=leg_indexes[row['position_leg_id']],
            cash_amount=row['cash_amount'],cash_currency_code=row['cash_currency_code']) for row in links_by_review[rid]]
        changes=[dict(ledger_id=row['ledger_id'],allocation_index=index,
            before_account_ref_id=flows[row['ledger_id']]['account_ref_id'],after_account_ref_id=overrides[row['ledger_id']])
            for index,row in enumerate(splits) if row['ledger_id'] in overrides]
        drafts.append(dict(type=original['behavior_type'] or 4,title=original['title'],case_code='ACCOUNT_CORRECTION',
            source_review_id=rid,copied_ledger_ids=[row['ledger_id'] for row in splits],account_changes=changes,
            new_positions=[],allocations=money,legs=copied_legs,position_allocations=links,transaction_ids=[],phase=None,
            account_bindings=bindings,duplicate_transactions=[decisions[fid] for fid in sorted(duplicates)]))
    return drafts,review_ids
