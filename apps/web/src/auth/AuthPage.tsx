import { useMutation } from '@tanstack/react-query'
import { Bot, Check, LockKeyhole, Network, ShieldCheck } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { PRODUCT_NAME } from '../config'
import { api } from '../lib/api'
import { Button } from '../components/ui/Button'
import { InlineError } from '../components/ui/Feedback'
import { useAuth } from './AuthProvider'

type AuthMode = 'login' | 'bootstrap'

export function AuthPage() {
  const { setAuthenticatedUser } = useAuth()
  const [mode, setMode] = useState<AuthMode>('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [displayName, setDisplayName] = useState('Axel')
  const [tenantName, setTenantName] = useState(PRODUCT_NAME)
  const [bootstrapToken, setBootstrapToken] = useState('')

  const mutation = useMutation({
    mutationFn: () =>
      mode === 'login'
        ? api.auth.login(email, password)
        : api.auth.bootstrap({
            email,
            password,
            display_name: displayName,
            tenant_name: tenantName,
            bootstrap_token: bootstrapToken,
          }),
    onSuccess: setAuthenticatedUser,
  })

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    mutation.mutate()
  }

  const changeMode = (next: AuthMode) => {
    setMode(next)
    mutation.reset()
  }

  return (
    <main className="auth-page">
      <section className="auth-story" aria-label={`Présentation de ${PRODUCT_NAME}`}>
        <div className="brand brand--large">
          <span className="brand__mark">
            <Bot aria-hidden="true" size={24} />
          </span>
          <span>{PRODUCT_NAME}</span>
        </div>
        <div className="auth-story__content">
          <span className="eyebrow eyebrow--light">Control Plane ACP</span>
          <h1>Vos agents travaillent là où vivent vos projets.</h1>
          <p>
            Orchestrez les agents Codex, Claude et ACP de vos LXC depuis une interface unique,
            sans exposer leurs environnements.
          </p>
          <ul className="feature-list">
            <li><Check size={17} /> Connexions worker sortantes et chiffrées</li>
            <li><Check size={17} /> Mentions structurées et traces persistantes</li>
            <li><Check size={17} /> Permissions humaines à chaque action sensible</li>
          </ul>
        </div>
        <div className="auth-story__network" aria-hidden="true">
          <Network size={22} />
          <span />
          <Bot size={22} />
          <span />
          <ShieldCheck size={22} />
        </div>
      </section>

      <section className="auth-panel">
        <div className="auth-card">
          <div className="auth-card__icon"><LockKeyhole aria-hidden="true" size={22} /></div>
          <h2>{mode === 'login' ? 'Bienvenue' : 'Créer le compte propriétaire'}</h2>
          <p>
            {mode === 'login'
              ? 'Connectez-vous au Control Plane.'
              : 'Cette opération ne peut être effectuée qu’une seule fois.'}
          </p>

          <div className="segmented" aria-label="Mode d’authentification">
            <button
              type="button"
              className={mode === 'login' ? 'is-active' : ''}
              aria-pressed={mode === 'login'}
              onClick={() => changeMode('login')}
            >
              Connexion
            </button>
            <button
              type="button"
              className={mode === 'bootstrap' ? 'is-active' : ''}
              aria-pressed={mode === 'bootstrap'}
              onClick={() => changeMode('bootstrap')}
            >
              Première installation
            </button>
          </div>

          <form className="form-stack" onSubmit={submit}>
            {mode === 'bootstrap' ? (
              <>
                <label>
                  Nom affiché
                  <input
                    required
                    autoComplete="name"
                    maxLength={160}
                    value={displayName}
                    onChange={(event) => setDisplayName(event.target.value)}
                  />
                </label>
                <label>
                  Organisation
                  <input
                    required
                    maxLength={160}
                    value={tenantName}
                    onChange={(event) => setTenantName(event.target.value)}
                  />
                </label>
              </>
            ) : null}
            <label>
              Adresse e-mail
              <input
                required
                type="email"
                autoComplete="email"
                placeholder="axel@example.com"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
              />
            </label>
            <label>
              Mot de passe
              <input
                required
                type="password"
                minLength={mode === 'bootstrap' ? 12 : 1}
                autoComplete={mode === 'bootstrap' ? 'new-password' : 'current-password'}
                value={password}
                onChange={(event) => setPassword(event.target.value)}
              />
              {mode === 'bootstrap' ? <small>12 caractères minimum</small> : null}
            </label>
            {mode === 'bootstrap' ? (
              <label>
                Jeton d’amorçage
                <input
                  required
                  type="password"
                  autoComplete="off"
                  value={bootstrapToken}
                  onChange={(event) => setBootstrapToken(event.target.value)}
                />
                <small>Valeur de <code>AGENT_FLEET_BOOTSTRAP_TOKEN</code></small>
              </label>
            ) : null}
            <InlineError error={mutation.error} />
            <Button type="submit" isLoading={mutation.isPending}>
              {mode === 'login' ? 'Se connecter' : 'Initialiser Agent Fleet'}
            </Button>
          </form>
          <p className="auth-card__security">
            Session sécurisée par cookie HttpOnly. Aucun jeton durable n’est stocké dans le navigateur.
          </p>
        </div>
      </section>
    </main>
  )
}
