import { Component, type ErrorInfo, type ReactNode } from "react";
import { ErrorState } from "./ui";

/**
 * Catches rendering errors in a page so one broken view shows an error card instead of
 * unmounting the whole app (a blank screen). `resetKey` (the route) clears the error on navigation.
 */
export class ErrorBoundary extends Component<{ children: ReactNode; resetKey?: string }, { error?: Error }> {
  state: { error?: Error } = {};

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("Page crashed:", error, info.componentStack);
  }

  componentDidUpdate(prev: { resetKey?: string }) {
    if (this.state.error && prev.resetKey !== this.props.resetKey) this.setState({ error: undefined });
  }

  render() {
    if (this.state.error) {
      return (
        <ErrorState
          error={new Error(`This page hit an error: ${this.state.error.message}`)}
          onRetry={() => this.setState({ error: undefined })}
        />
      );
    }
    return this.props.children;
  }
}
