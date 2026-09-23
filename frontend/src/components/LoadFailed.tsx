interface Props {
  error: Error;
  /** What could not be loaded, in the operator's words: "transfers". */
  what: string;
}

/**
 * A failed read, stated plainly, so a panel whose empty state is good news never
 * shows that state by mistake. Clears when the query's retries succeed.
 */
export default function LoadFailed({ error, what }: Props) {
  return (
    <p className={'text-sm text-danger'} role={'alert'}>
      {`Could not load ${what}: ${error.message}. Retrying.`}
    </p>
  );
}
