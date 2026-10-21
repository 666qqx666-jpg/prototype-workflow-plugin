import {validateIndependentReview} from './validate-independent-review.mjs';

// Keep Snapshot revalidation on the same evidence-resolving path as the CLI.
export function validateSnapshotReview(input) {
  return validateIndependentReview(input);
}
