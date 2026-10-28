"""Acquisition functions and candidate selection."""

from __future__ import annotations

from typing import Any


def propose_expected_improvement_batch(
    *,
    model: Any,
    train_y: Any,
    bounds: Any,
    batch_size: int,
    torch: Any,
    seed: int,
    mc_samples: int,
    num_restarts: int,
    raw_samples: int,
    diagnostics: dict | None = None,
) -> Any:
    from botorch.acquisition.logei import qLogExpectedImprovement
    from botorch.optim import optimize_acqf
    from botorch.sampling.normal import SobolQMCNormalSampler

    sampler = SobolQMCNormalSampler(
        sample_shape=torch.Size([mc_samples]),
        seed=seed,
    )
    acquisition = qLogExpectedImprovement(
        model=model,
        best_f=train_y.max(),
        sampler=sampler,
    )
    candidates, _ = optimize_acqf(
        acq_function=acquisition,
        bounds=bounds,
        q=batch_size,
        num_restarts=num_restarts,
        raw_samples=raw_samples,
        options={"batch_limit": 5, "maxiter": 200},
        sequential=batch_size > 1,
    )
    if diagnostics is not None:
        try:
            from .decision_statistics import summarize_model
            generator = torch.Generator(device=bounds.device).manual_seed(seed + 1)
            alternatives = bounds[0] + (bounds[1] - bounds[0]) * torch.rand(
                (64, bounds.shape[-1]), generator=generator, dtype=bounds.dtype, device=bounds.device)
            diagnostics.update(summarize_model(model, candidates.detach(), alternatives,
                                               acquisition, train_y, torch))
        except Exception as exc:
            # Diagnostic failure must not change candidate selection or stop BO.
            diagnostics['diagnostics_error'] = str(exc)
    return candidates.detach().cpu()
