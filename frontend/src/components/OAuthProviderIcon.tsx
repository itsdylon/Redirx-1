import { FaGithub } from 'react-icons/fa';
import { FcGoogle } from 'react-icons/fc';

type OAuthProvider = 'google' | 'github';

interface OAuthProviderIconProps {
  provider: OAuthProvider;
  className?: string;
}

export function OAuthProviderIcon({ provider, className }: OAuthProviderIconProps) {
  if (provider === 'google') {
    return (
      <FcGoogle
        aria-hidden="true"
        focusable="false"
        className={className}
      />
    );
  }

  return (
    <FaGithub
      aria-hidden="true"
      focusable="false"
      className={className}
    />
  );
}
