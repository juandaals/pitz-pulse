"""Review-queue rule shared by the API (Spec 02) and eval scoring (Spec 03): one comparator."""


def needs_review(confianza: float, threshold: float) -> bool:
    return confianza < threshold
