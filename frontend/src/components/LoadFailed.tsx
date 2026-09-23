interface Props {
  error: Error;
  /** What could not be loaded, in the operator's words: "transfers". */
  what: string;
}

/**
 * A read that failed, said plainly instead of looking empty.
 *
 * The difference matters most on the panels whose empty state is good news:
 * "Nothing to report" under Reconciliation, or no dead letters, shown because
 * the request failed rather than because nothing is wrong, is the console
 * telling an operator the opposite of the truth. The query keeps retrying on
 * its own cadence, so this clears by itself when the API is back.
 */
export default function LoadFailed({ error, what }: Props) {
  return (
    <p className={'text-sm text-danger'} role={'alert'}>
      {`Could not load ${what}: ${error.message}. Retrying.`}
    </p>
  );
}
