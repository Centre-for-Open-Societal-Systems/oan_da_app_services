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
        // The DA node. Jenkins SSHes in to build the image there (it has the disk and RAM a
        // Frappe build needs); the release itself runs on the oan_dev k3s node.
        DA_NODE         = '65.0.132.192'
        HELM_RELEASE    = 'da-services'
        // oan_dev's private address: APISIX binds its ports 80/443, Verify calls it from the VPN agent.
        OAN_DEV_EDGE_IP = '10.0.1.131'
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
                            namespace: 'da-app',
                            verifyHost: 'da-services.development.oanstaging.com'
                        ]
                    ]
                    def target = targets[env.BRANCH_NAME]
                    env.DEPLOY        = target ? 'true' : 'false'
                    env.TARGET_ENV    = target ? env.BRANCH_NAME : 'develop'
                    def t = target ?: targets.develop
                    env.K8S_NAMESPACE = t.namespace
                    env.VERIFY_HOST   = t.verifyHost

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

        // develop -> Helm release `da-services` on the oan_dev k3s node (chart helm/oan-da-services).
        // First run installs, later runs upgrade; the chart generates its admin and JWT Secrets.
        stage('develop → oan_dev (helm)') {
            when { beforeAgent true; environment name: 'DEPLOY', value: 'true' }
            agent { label 'oan-ci-deploy' }   // VPN agent with helm and kubectl that reaches the oan_dev API
            options { timeout(time: 45, unit: 'MINUTES') }
            steps {
                checkout scm
                withCredentials([file(credentialsId: 'oan-dev-kubeconfig', variable: 'KUBECONFIG')]) {
                    sh 'DA_NAMESPACE="$K8S_NAMESPACE" DA_RELEASE="$HELM_RELEASE" bash ci/deploy-helm-dev.sh'
                }
            }
        }

        // develop -> APISIX routes (chart apisix/k8s) on the oan_dev node, after the app deploy.
        // Runs only when the routes or the gateway values changed.
        stage('develop → gateway (apisix)') {
            when {
                beforeAgent true
                allOf {
                    environment name: 'DEPLOY', value: 'true'
                    anyOf {
                        changeset 'apisix/**'
                        changeset 'da_services/api/**'
                        changeset 'helm/environments/dev/oan-da-services-gateway.yaml'
                    }
                }
            }
            agent { label 'oan-ci-deploy' }
            options { timeout(time: 15, unit: 'MINUTES') }
            steps {
                checkout scm
                withCredentials([file(credentialsId: 'oan-dev-kubeconfig', variable: 'KUBECONFIG')]) {
                    sh 'DA_NAMESPACE="$K8S_NAMESPACE" bash ci/deploy-gateway.sh'
                }
            }
        }

        // Through APISIX on oan_dev, by its private address: public DNS may still point elsewhere.
        stage('Verify') {
            when { beforeAgent true; environment name: 'DEPLOY', value: 'true' }
            agent { label 'oan-ci-deploy' }
            steps {
                sh '''
                    url="https://$VERIFY_HOST/v1/da-services/health"
                    for i in $(seq 1 10); do
                        body=$(curl -sL -m 10 --resolve "$VERIFY_HOST:443:$OAN_DEV_EDGE_IP" "$url" || true)
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
            echo "da-services ${env.BRANCH_NAME} build failed (namespace ${env.K8S_NAMESPACE}). Check the stage logs above; 'helm history ${env.HELM_RELEASE} -n ${env.K8S_NAMESPACE}' on oan_dev shows the release state."
        }
    }
}
