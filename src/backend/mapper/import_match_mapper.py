"""Exact candidate scope and complete origin proofs in one read snapshot."""
from sqlalchemy import select

from backend.entity import TransactionFact
from backend.mapper.import_batch_mapper import ImportBatchMapper, MAX_EVIDENCE_CANDIDATES
from backend.error import TargetIntakeError
from backend.mapper.candidate_mapper import CandidateMapper
from backend.mapper.review_command_mapper import chunks


def match_limit():
    raise TargetIntakeError(422, "exact source candidates exceed the matching budget", code="IMPORT_MATCH_LIMIT",
        details=dict(action="NARROW_IMPORT_SCOPE", limit=MAX_EVIDENCE_CANDIDATES))


class ImportMatchMapper(ImportBatchMapper):
    def suggestion_reviews(self, ids):
        """Bounded IN reads shared by all unique suggestions, not per row SQL."""
        result = []
        for batch in chunks(sorted(set(ids))):
            result.extend(CandidateMapper(self.db).current_reviews([dict(id=id) for id in batch]))
            if len(result) > MAX_EVIDENCE_CANDIDATES:
                raise TargetIntakeError(413,"complete candidate summaries exceed read budget",code="DETAIL_LIMIT")
        return result

    def exact_page(self, values, source, kind, request):
        """Count all scoped identities; never filter by financial eligibility.

        Unknown origin proofs remain visible, blocked, in either directory.
        This avoids presenting a missing proof as 'no duplicate'. Only page
        proofs are retained after scanning; raw envelopes are streamed by the
        existing origin verifier, not embedded in results or the preview cache.
        Caller supplies the shared two-second/50k matching budget.
        """
        f = TransactionFact
        statement = select(f.__table__).where(
            f.occurred_time == values["occurred_time"], f.amount == values["amount"],
            f.currency_code == values["currency_code"], f.cash_direction == values["cash_direction"]
        ).order_by(f.occurred_time.desc(), f.id.asc()).limit(MAX_EVIDENCE_CANDIDATES + 1)
        facts = [dict(row) for row in self.db.execute(statement).mappings()]
        if len(facts) > MAX_EVIDENCE_CANDIDATES:
            match_limit()
        self._match_candidate_count += len(facts)
        if self._match_candidate_count > MAX_EVIDENCE_CANDIDATES:
            match_limit()
        start = (request.page_index - 1) * request.page_size
        total, page = 0, []
        # Bounded IN batches, not one query per source row/candidate. The
        # existing target verifier's 2000-ID command guard is not a new total
        # match-directory cap; its streaming 50k work counter remains shared.
        for offset in range(0, len(facts), 2000):
            batch = facts[offset:offset + 2000]
            proofs = self._evidence_targets([fact["id"] for fact in batch])
            for fact in batch:
                proof = proofs[fact["id"]]
                if not proof.get("issue") and source is not None:
                    same = (source["source_type"], source["values"]["account_code"]) == (
                        proof["source_type"], proof["values"]["account_code"])
                    if same != (kind == "SAME_SOURCE"):
                        continue
                if start <= total < start + request.page_size:
                    page.append((fact, proof))
                total += 1
        return page, total
