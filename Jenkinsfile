// CI/CD pipeline for Gearhead FEA (WebFEA) - runs on a Jenkins that lives on the same Linux server
// that hosts the app. Read docs/CI_CD_JENKINS.md for the full explanation and the server setup.
//
//   push to git -> build both images -> run the backend tests in the image -> (main only)
//   write .env from a Jenkins credential -> docker compose up -d --build -> smoke-test /api/health
//
// Requirements on the Jenkins node: Docker Engine + the Compose plugin, `jenkins` user in the `docker` group,
// and a "Secret file" credential with the id `webfea-env` holding the production .env (see .env.example).
pipeline {
    agent any

    options {
        timestamps()
        timeout(time: 45, unit: 'MINUTES')
        buildDiscarder(logRotator(numToKeepStr: '20'))
        disableConcurrentBuilds()            // two deploys at once would fight over the same containers
    }

    // Fallback when no webhook is configured: look for new commits every ~5 minutes.
    triggers { pollSCM('H/5 * * * *') }

    parameters {
        booleanParam(name: 'DEPLOY', defaultValue: true,
                     description: 'Deploy after the tests pass (only ever happens on the main branch).')
    }

    environment {
        COMPOSE_PROJECT_NAME = 'webfea'      // fixed name => the same containers and the same pgdata volume on every deploy
        BACKEND_IMAGE        = "webfea-backend:ci-${env.BUILD_NUMBER}"
        FRONTEND_IMAGE       = "webfea-frontend:ci-${env.BUILD_NUMBER}"
        ENV_CREDENTIAL_ID    = 'webfea-env'
    }

    stages {
        stage('Preflight') {
            steps {
                sh '''
                    set -eu
                    docker version --format 'docker {{.Server.Version}}'
                    docker compose version
                    git log -1 --oneline
                '''
            }
        }

        stage('Build images') {
            parallel {
                stage('Backend image') {
                    steps { sh 'docker build -t "$BACKEND_IMAGE" backend' }
                }
                stage('Frontend image') {
                    // The Dockerfile runs `npm ci` and `npm run build`, so a frontend that does not compile fails here.
                    steps { sh 'docker build -t "$FRONTEND_IMAGE" frontend' }
                }
            }
        }

        stage('Backend tests') {
            steps {
                // Tests are not baked into the image (.dockerignore), so mount them read-only and add pytest at run time.
                // Runs as the image's own non-root user; HOME=/tmp gives pip a writable place. SQLite is used by default.
                sh '''
                    set -eu
                    rm -rf test-results && mkdir -p test-results && chmod 777 test-results
                    docker run --rm \
                        -e HOME=/tmp -e PYTHONDONTWRITEBYTECODE=1 \
                        -v "$PWD/backend/tests:/app/tests:ro" \
                        -v "$PWD/test-results:/results" \
                        "$BACKEND_IMAGE" \
                        sh -c "pip install --user --quiet pytest && python -m pytest tests -q -p no:cacheprovider --junitxml=/results/backend.xml"
                '''
            }
            post { always { junit allowEmptyResults: true, testResults: 'test-results/*.xml' } }
        }

        stage('Deploy') {
            when {
                allOf {
                    expression { params.DEPLOY }
                    expression { (env.BRANCH_NAME ?: env.GIT_BRANCH) in ['main', 'origin/main'] }
                }
            }
            steps {
                withCredentials([file(credentialsId: env.ENV_CREDENTIAL_ID, variable: 'ENV_FILE')]) {
                    sh '''
                        set -eu
                        install -m 600 "$ENV_FILE" .env
                        docker compose config --quiet                  # fails early on a missing/invalid variable
                        docker compose up -d --build --remove-orphans
                    '''
                }
            }
        }

        stage('Smoke test') {
            when {
                allOf {
                    expression { params.DEPLOY }
                    expression { (env.BRANCH_NAME ?: env.GIT_BRANCH) in ['main', 'origin/main'] }
                }
            }
            steps {
                sh '''
                    set -eu
                    PORT=$(grep -E '^HTTP_PORT=' .env | cut -d= -f2 || true)
                    PORT=${PORT:-5173}
                    for i in $(seq 1 30); do
                        if curl -fsS "http://localhost:${PORT}/api/health"; then echo; echo "healthy after $i attempt(s)"; exit 0; fi
                        sleep 3
                    done
                    echo "health check failed"; exit 1
                '''
            }
        }
    }

    post {
        failure {
            sh 'docker compose ps || true; docker compose logs --tail=80 backend || true'
        }
        cleanup {
            // never leave production secrets in the workspace; drop this build's CI-tagged images and old dangling layers
            sh '''
                rm -f .env
                docker rmi "$BACKEND_IMAGE" "$FRONTEND_IMAGE" || true
                docker image prune -f || true
            '''
        }
    }
}
