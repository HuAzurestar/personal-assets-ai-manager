"""Linear exact-scope suggestion index; no identity decisions or publication.

Count all candidates, including unknown origin proofs. Never drop a blocked
candidate to make another look unique. Each bucket keeps only two exemplars:
enough to find a singleton after removing the source's own planned identity.
"""
from collections import defaultdict

from backend.core.import_risk import risk_signature


def source_identity(proof):
    return None if proof.get("issue") else (proof["source_type"],proof["values"]["account_code"])


class ExactPairingIndex:
    def __init__(self, targets):
        self.scopes = defaultdict(dict)
        self.totals = defaultdict(int)
        self.representatives = defaultdict(list)
        for target in targets:
            signature = risk_signature(target["values"])
            identity = source_identity(target["proof"])
            bucket = self.scopes[signature].setdefault(identity,[0,[]])
            bucket[0] += 1
            self.totals[signature] += 1
            if len(bucket[1]) < 2:
                bucket[1].append(target)
            # Two distinct source partitions suffice when excluding one.
            representatives = self.representatives[signature]
            if len(representatives) < 2 and all(source_identity(item["proof"]) != identity for item in representatives):
                representatives.append(target)

    def match(self, values, proof, kind, own_identity):
        signature, source = risk_signature(values),source_identity(proof)
        partitions = self.scopes.get(signature,{})
        same_count,same = partitions.get(source,(0,[]))
        if kind == "CROSS_SOURCE":
            count = self.totals.get(signature,0) - same_count
            candidate = next((item for item in self.representatives.get(signature,[])
                if source_identity(item["proof"]) != source),None)
        else:
            unknown_count,unknown = partitions.get(None,(0,[]))
            # Own local identity exists only in its proven source partition.
            count = same_count + unknown_count - int(own_identity is not None)
            candidate = next((item for item in same + unknown if item["identity"] != own_identity),None)
        return count,candidate if count == 1 else None
