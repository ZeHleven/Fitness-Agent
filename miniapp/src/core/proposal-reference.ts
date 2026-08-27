import type {
  PlanAdjustmentProposalReadResponse,
  PlanAdjustmentProposalReference,
  PlanAdjustmentProposalStatus
} from '../types/plan-adjustment-proposal'


const PROPOSAL_STATUSES: ReadonlySet<string> = new Set([
  'pending_confirmation',
  'applied',
  'rejected',
  'expired',
  'stale',
  'failed'
])

function isProposalStatus (value: unknown): value is PlanAdjustmentProposalStatus {
  return typeof value === 'string' && PROPOSAL_STATUSES.has(value)
}

export function proposalReferenceFromUnknown (
  value: unknown
): PlanAdjustmentProposalReference | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  const proposal = value as Record<string, unknown>
  if (
    typeof proposal.id !== 'string' ||
    proposal.proposal_type !== 'plan_adjustment_v1' ||
    !isProposalStatus(proposal.status) ||
    typeof proposal.version !== 'number' ||
    !Number.isInteger(proposal.version) ||
    proposal.version < 1 ||
    typeof proposal.expires_at !== 'string' ||
    typeof proposal.payload_fingerprint !== 'string' ||
    !/^[0-9a-f]{64}$/.test(proposal.payload_fingerprint)
  ) return null
  return proposal as unknown as PlanAdjustmentProposalReference
}

export function proposalReferenceFromRead (
  proposal: PlanAdjustmentProposalReadResponse
): PlanAdjustmentProposalReference {
  return {
    id: proposal.id,
    proposal_type: proposal.proposal_type,
    status: proposal.status,
    version: proposal.version,
    expires_at: proposal.expires_at,
    payload_fingerprint: proposal.payload_fingerprint
  }
}

export function unavailableProposalReference (
  proposal: PlanAdjustmentProposalReference
): PlanAdjustmentProposalReference {
  return { ...proposal, status: 'missing' }
}
