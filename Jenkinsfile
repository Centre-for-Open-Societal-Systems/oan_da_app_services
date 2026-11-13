pipeline {
    agent any

    options {
        disableConcurrentBuilds()
        timeout(time: 60, unit: 'MINUTES')
    }

    environment {
        AWS_REGION      = 'ap-south-1'
        AWS_ACCOUNT_ID  = '379220350808'
        IMAGE_REGISTRY  = '379220350808.dkr.ecr.ap-south-1.amazonaws.com'
        IMAGE_NAME      = 'da-services-backend'   // must exist as an ECR repository first
        IMAGE_REPO      = "${IMAGE_REGISTRY}/${IMAGE_NAME}"
        // The DA cluster node. Jenkins SSHes in to build the image there and to run helm.
        DA_NODE         = '65.0.132.192'
        HELM_RELEASE    = 'services-backend'
        CHART_DIR       = '/home/ubuntu/da/helm/charts/da-frappe'
        // da_services' required_apps, installed into the bench before it. The branch is
        // resolved to a commit at build time so a new commit there rebuilds the bench layer.
        AUTH_APP_URL    = 'https://github.com/Centre-for-Open-Societal-Systems/oan_auth_service'
        AUTH_APP_BRANCH = 'develop'
    }

    stages {

        stage('Checkout') {
            steps {
                checkout scm
                script {
                    // Branches that build and deploy. Every other branch only runs the checks.
                    // staging is not listed yet: the staging namespace has no services-backend /
                    // services-backend-deps releases (create them with the Ansible apps role first).
                    def targets = [
                        develop: [
                            namespace: 'develop',
                            verifyUrl: 'https://da-backend-development.oanstaging.com'
                        ]
                    ]
                    def target = targets[env.BRANCH_NAME]
                    env.DEPLOY        = target ? 'true' : 'false'
                    env.TARGET_ENV    = target ? env.BRANCH_NAME : 'develop'
                    def t = target ?: targets.develop
                    env.K8S_NAMESPACE = t.namespace
                    env.VERIFY_URL    = t.verifyUrl

                    env.GIT_SHA = sh(returnStdout: true, script: 'git rev-parse HEAD').trim()
                    env.IMAGE_TAG_BUILD = "${env.TARGET_ENV}-${env.BUILD_NUMBER}-${env.GIT_SHA.substring(0, 7)}"
                    env.FULL_IMAGE      = "${env.IMAGE_REPO}:${env.IMAGE_TAG_BUILD}"
                    echo "Branch ${env.BRANCH_NAME} -> deploy=${env.DEPLOY}, namespace ${env.K8S_NAMESPACE}, image ${env.FULL_IMAGE}"
                }
            }
        }

        stage('Lint') {
            steps {
                // Same checks as the GitHub Actions lint job, which also runs the latest ruff.
                // The bench test suite needs MariaDB and Redis; it runs in GitHub Actions (ci.yml).
                sh '''
                    docker run --rm --pull always --user "$(id -u):$(id -g)" \
                        -v "$PWD":/io -w /io ghcr.io/astral-sh/ruff:latest check --no-cache .
                    docker run --rm --user "$(id -u):$(id -g)" \
                        -v "$PWD":/io -w /io ghcr.io/astral-sh/ruff:latest format --check --no-cache .
                '''
            }
        }

        stage('Build image') {
            when { environment name: 'DEPLOY', value: 'true' }
            steps {
                script {
                    env.AUTH_APP_REV = sh(returnStdout: true,
                        script: 'git ls-remote "$AUTH_APP_URL" "refs/heads/$AUTH_APP_BRANCH" | cut -f1').trim()
                    if (!env.AUTH_APP_REV) {
                        error("Cannot resolve ${env.AUTH_APP_URL} ${env.AUTH_APP_BRANCH}")
                    }
                }
                withCredentials([sshUserPrivateKey(
                    credentialsId: 'da-node-ssh-key',
                    keyFileVariable: 'SSH_KEY',
                    usernameVariable: 'SSH_USER'
                )]) {
                    // Built with Docker on the DA node, not here: bench init plus the asset build
                    // need several GB of RAM and disk that the Jenkins controller does not have.
                    // The build context is this checkout, .git included (bench clones the app from it).
                    sh '''
BUILD_DIR="/home/ubuntu/da/build/$HELM_RELEASE-$TARGET_ENV"
tar -czf - . | ssh -o StrictHostKeyChecking=no -i "$SSH_KEY" "$SSH_USER@$DA_NODE" \
    "rm -rf '$BUILD_DIR' && mkdir -p '$BUILD_DIR' && tar -xzf - -C '$BUILD_DIR'"

ssh -o StrictHostKeyChecking=no -i "$SSH_KEY" "$SSH_USER@$DA_NODE" \
    bash -s -- "$BUILD_DIR" "$FULL_IMAGE" "$IMAGE_REPO:$TARGET_ENV" "$GIT_SHA" \
        "$AUTH_APP_URL" "$AUTH_APP_BRANCH" "$AUTH_APP_REV" <<'ENDSSH'
set -euo pipefail
CTX="$1"; IMAGE="$2"; MOVING="$3"; COMMIT="$4"; AUTH_URL="$5"; AUTH_BRANCH="$6"; AUTH_REV="$7"

# The build shares the root disk with k3s, and kubelet starts evicting pods below 10% free.
avail_gb=$(df -BG --output=avail / | tail -1 | tr -dc '0-9')
if [ "$avail_gb" -lt 15 ]; then
    echo "Only ${avail_gb}G free on the DA node's root disk; a Frappe build can use ~10G."
    echo "Free space first, e.g. 'sudo docker system prune -af' (k3s has its own image store)."
    exit 1
fi

APPS_JSON=$(printf '[{"url": "%s", "branch": "%s"}]' "$AUTH_URL" "$AUTH_BRANCH")
echo "Building $IMAGE (da_services $COMMIT, oan_auth_service $AUTH_BRANCH@$AUTH_REV)"
sudo docker build --pull \
    -t "$IMAGE" \
    -t "$MOVING" \
    --build-arg "EXTRA_APPS_JSON=$APPS_JSON" \
    --build-arg "APPS_REVISION=$AUTH_REV" \
    --label "org.opencontainers.image.revision=$COMMIT" \
    --label "org.opencontainers.image.source=https://github.com/Centre-for-Open-Societal-Systems/oan_da_app_services" \
    --label "oan.auth-service.revision=$AUTH_REV" \
    "$CTX"
rm -rf "$CTX"
ENDSSH
                    '''
                }
            }
        }

        stage('Push image') {
            when { environment name: 'DEPLOY', value: 'true' }
            steps {
                withCredentials([[
                    $class: 'AmazonWebServicesCredentialsBinding',
                    credentialsId: 'aws-ecr-creds'
                ], sshUserPrivateKey(
                    credentialsId: 'da-node-ssh-key',
                    keyFileVariable: 'SSH_KEY',
                    usernameVariable: 'SSH_USER'
                )]) {
                    // The node has no AWS credentials: Jenkins hands it a 12h ECR token, and the
                    // push logs out again when it is done.
                    sh '''
aws ecr get-login-password --region "$AWS_REGION" \
    | ssh -o StrictHostKeyChecking=no -i "$SSH_KEY" "$SSH_USER@$DA_NODE" \
        "sudo docker login --username AWS --password-stdin '$IMAGE_REGISTRY'"

ssh -o StrictHostKeyChecking=no -i "$SSH_KEY" "$SSH_USER@$DA_NODE" \
    bash -s -- "$FULL_IMAGE" "$IMAGE_REPO:$TARGET_ENV" "$IMAGE_REGISTRY" <<'ENDSSH'
set -euo pipefail
IMAGE="$1"; MOVING="$2"; REGISTRY="$3"
trap 'sudo docker logout "$REGISTRY" > /dev/null' EXIT
sudo docker push "$IMAGE"
sudo docker push "$MOVING"

# k3s pulls from ECR into its own containerd, so Docker's copy is no longer needed.
# Cap the build cache too: the node's root disk also holds k3s images and volumes.
sudo docker rmi "$IMAGE" "$MOVING" > /dev/null
sudo docker builder prune -f --max-used-space 10gb > /dev/null
ENDSSH
                    '''
                }
            }
        }

        stage('Deploy (helm)') {
            when { environment name: 'DEPLOY', value: 'true' }
            steps {
                withCredentials([sshUserPrivateKey(
                    credentialsId: 'da-node-ssh-key',
                    keyFileVariable: 'SSH_KEY',
                    usernameVariable: 'SSH_USER'
                )]) {
                    // Same chart Ansible uses; --reuse-values keeps the site config, ingress host and
                    // resources of the live release and changes only the image and pull secret.
                    // The chart's pre-upgrade setup Job migrates the site before the pods roll.
                    // No --rollback-on-failure (as in the Ansible role): a rollback can't undo a
                    // migration the setup Job already ran.
                    sh '''
ssh -o StrictHostKeyChecking=no -i "$SSH_KEY" "$SSH_USER@$DA_NODE" \
    bash -s -- "$HELM_RELEASE" "$CHART_DIR" "$K8S_NAMESPACE" "$IMAGE_REPO" "$IMAGE_TAG_BUILD" <<'ENDSSH'
set -euo pipefail
RELEASE="$1"; CHART="$2"; NS="$3"; REPO="$4"; TAG="$5"
export PATH="$PATH:/usr/local/bin:/snap/bin"
export KUBECONFIG="${KUBECONFIG:-$HOME/.kube/config}"

# Fail fast on a wrong release/namespace instead of creating a second release
helm status "$RELEASE" -n "$NS" > /dev/null
kubectl get secret ecr-regcred -n "$NS" > /dev/null || { echo "secret ecr-regcred missing in $NS (pull-secret CronJob not set up?)"; exit 1; }

echo "Deploying $REPO:$TAG to release $RELEASE ($NS)"
if ! helm upgrade "$RELEASE" "$CHART" -n "$NS" --reuse-values \
    --set-string "image.repository=$REPO" \
    --set-string "image.tag=$TAG" \
    --set-string "image.pullPolicy=IfNotPresent" \
    --set "imagePullSecrets[0].name=ecr-regcred" \
    --wait --timeout 20m; then
    echo "helm upgrade failed. Setup Job log:"
    kubectl logs -n "$NS" "job/$RELEASE-setup" --tail=200 || true
    kubectl get pods -n "$NS" -l "app.kubernetes.io/instance=$RELEASE" || true
    exit 1
fi
ENDSSH
                    '''
                }
            }
        }

        stage('Verify') {
            when { environment name: 'DEPLOY', value: 'true' }
            steps {
                sh '''
                    url="$VERIFY_URL/api/v1/da-services/health"
                    for i in $(seq 1 10); do
                        body=$(curl -skL -m 10 "$url" || true)
                        echo "attempt $i: $url -> $body"
                        echo "$body" | grep -q '"status": *"healthy"' && exit 0
                        sleep 6
                    done
                    echo "da-services did not report healthy at $url"
                    exit 1
                '''
            }
        }
    }

    post {
        failure {
            echo "da-services ${env.BRANCH_NAME} build failed (namespace ${env.K8S_NAMESPACE}). Check the stage logs above; 'helm history ${env.HELM_RELEASE} -n ${env.K8S_NAMESPACE}' on the DA node shows the release state."
        }
    }
}
