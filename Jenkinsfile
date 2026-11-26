pipeline {
    agent any

    options {
        disableConcurrentBuilds()
        timeout(time: 90, unit: 'MINUTES')
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
        // develop is also installed on the oan_dev k3s node (stage "develop → oan_dev (helm)"), from the same
        // image pushed again under this repository with the tag <__version__>-develop.<build>.
        ECR_REPO        = 'eoan/oan-da-services/da-services'
        // oan_dev's private address: APISIX binds its ports 80/443, the oan_dev verify calls it from the VPN agent.
        OAN_DEV_EDGE_IP = '10.0.1.131'
    }

    stages {

        stage('Checkout') {
            steps {
                checkout scm
                script {
                    // Branches that build and deploy. Every other branch only runs the checks.
                    // Each target needs the services-backend / services-backend-deps releases and the
                    // ecr-regcred secret in its namespace on the DA node (Ansible apps role); the
                    // Deploy stage fails fast if they are missing.
                    def targets = [
                        develop: [
                            namespace: 'develop',
                            verifyUrl: 'https://da-backend-development.oanstaging.com'
                        ],
                        staging: [
                            namespace: 'staging',
                            verifyUrl: 'https://da-backend-staging.oanstaging.com'
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
                    // The Helm chart version, also the image tag in eoan/oan-da-services/da-services (ci/package_chart.py version):
                    //   develop <__version__>-develop.<build>, pushed by "Push image" (HELM_IMAGE);
                    //   staging <__version__>-staging.<build>: the develop image of the same source tree, tagged again
                    //   by "Package chart" (promote). Nothing is rebuilt for the QA cluster.
                    env.HELM_IMAGE = ''
                    if (env.BRANCH_NAME == 'develop' || env.BRANCH_NAME == 'staging') {
                        env.IMMUTABLE_TAG = sh(returnStdout: true, script: 'python3 ci/package_chart.py version').trim()
                    }
                    if (env.BRANCH_NAME == 'develop') {
                        env.HELM_IMAGE    = "${env.IMAGE_REGISTRY}/${env.ECR_REPO}:${env.IMMUTABLE_TAG}"
                    }
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
                    // push logs out again when it is done. develop also pushes HELM_IMAGE for oan_dev,
                    // here because the node deletes its copy of the image after the push.
                    sh '''
if [ -n "$HELM_IMAGE" ]; then
    # ECR does not create a repository on push.
    aws ecr describe-repositories --region "$AWS_REGION" --repository-names "$ECR_REPO" >/dev/null 2>&1 \
        || aws ecr create-repository --region "$AWS_REGION" --repository-name "$ECR_REPO" >/dev/null
fi

aws ecr get-login-password --region "$AWS_REGION" \
    | ssh -o StrictHostKeyChecking=no -i "$SSH_KEY" "$SSH_USER@$DA_NODE" \
        "sudo docker login --username AWS --password-stdin '$IMAGE_REGISTRY'"

ssh -o StrictHostKeyChecking=no -i "$SSH_KEY" "$SSH_USER@$DA_NODE" \
    bash -s -- "$FULL_IMAGE" "$IMAGE_REPO:$TARGET_ENV" "$IMAGE_REGISTRY" "$HELM_IMAGE" <<'ENDSSH'
set -euo pipefail
IMAGE="$1"; MOVING="$2"; REGISTRY="$3"; HELM_IMAGE="${4:-}"
trap 'sudo docker logout "$REGISTRY" > /dev/null' EXIT
sudo docker push "$IMAGE"
sudo docker push "$MOVING"
if [ -n "$HELM_IMAGE" ]; then
    sudo docker tag "$IMAGE" "$HELM_IMAGE"
    sudo docker push "$HELM_IMAGE"
    sudo docker rmi "$HELM_IMAGE" > /dev/null
fi

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

        // Flow (develop): Checkout (version) -> Build image -> Push image -> Package chart -> deploy -> Publish chart.
        // helm/oan-da-services is zipped with this build's image before the oan_dev deploy; the deploy installs
        // that archive, and the same archive is published to oan-helm afterwards.
        // staging: `promote` instead of `package` (as oan_a2c): the chart develop built from this source tree, its image
        // tagged <__version__>-staging.<build> in ECR. Fails if develop never built this tree: merge develop into staging.
        stage('Package chart') {
            when { anyOf { branch 'develop'; branch 'staging' } }
            steps {
                script {
                    env.IMAGE_TAG = env.IMMUTABLE_TAG
                    env.SOURCE_COMMIT = env.GIT_SHA
                    env.COMMIT_SUBJECT = sh(returnStdout: true, script: 'git log -1 --pretty=%s').trim()
                    env.IMAGE_REPOSITORY = "${env.IMAGE_REGISTRY}/${env.ECR_REPO}"
                    if (env.BRANCH_NAME == 'staging') {
                        // The workspace holds the staging branch only; promote needs the develop commits the chart index names.
                        withCredentials([gitUsernamePassword(credentialsId: scm.userRemoteConfigs[0].credentialsId, gitToolName: 'Default')]) {
                            sh 'git fetch --quiet --no-tags origin +refs/heads/develop:refs/remotes/origin/develop'
                        }
                    }
                }
                // promote tags the image in ECR (batch-get-image + put-image) with the same credential as "Push image".
                withCredentials([[
                    $class: 'AmazonWebServicesCredentialsBinding',
                    credentialsId: 'aws-ecr-creds'
                ]]) {
                    sh "python3 ci/package_chart.py ${env.BRANCH_NAME == 'staging' ? 'promote' : 'package'}"
                }
                // The deploy stages run on the VPN deploy agent, which gets the archive from here.
                stash name: 'built-chart', includes: 'built-chart/**'
            }
        }

        // The routes chart (apisix/k8s) is packaged on every develop and staging build: "develop → gateway (apisix)"
        // decides at deploy time whether to install it (gateway files changed, or the release is missing), so its
        // unstash must always find the archive. Packaging only copies apisix/k8s; publishing stays conditional.
        stage('Package gateway chart') {
            when { anyOf { branch 'develop'; branch 'staging' } }
            steps {
                sh 'python3 ci/package_chart.py gateway-package'
                stash name: 'built-gateway-chart', includes: 'built-gateway-chart/**'
            }
        }

        // ---- develop -> Helm release `da-services` on the oan_dev k3s node ----
        // Runs after the DA node deploy above, so a problem here never blocks it. Same image, pushed
        // under eoan/oan-da-services/da-services:<__version__>-develop.<build> by "Push image".
        stage('develop → oan_dev (helm)') {
            when { beforeAgent true; branch 'develop' }
            agent { label 'oan-ci-deploy' }   // VPN agent with helm and kubectl that reaches the oan_dev API
            options { timeout(time: 45, unit: 'MINUTES') }
            steps {
                checkout scm
                unstash 'built-chart'
                withCredentials([file(credentialsId: 'oan-dev-kubeconfig', variable: 'KUBECONFIG')]) {
                    sh 'bash ci/deploy-helm-dev.sh'
                }
            }
        }

        // develop -> APISIX routes (chart apisix/k8s) on the oan_dev node, after the app deploy.
        // Installs when the merged change touched the routes or the gateway values, or when the release is
        // missing. Not `changeset`: Jenkins compares with the previous build, so a branch's first build has an
        // empty changeset and skipped the install. The diff is against the first parent, as for a merge
        // commit; a shallow clone without it counts as changed (the install is idempotent).
        stage('develop → gateway (apisix)') {
            when { beforeAgent true; branch 'develop' }
            agent { label 'oan-ci-deploy' }
            options { timeout(time: 15, unit: 'MINUTES') }
            steps {
                checkout scm
                unstash 'built-gateway-chart'
                withCredentials([file(credentialsId: 'oan-dev-kubeconfig', variable: 'KUBECONFIG')]) {
                    sh '''
                        changed=$(git diff --name-only HEAD^1 HEAD 2>/dev/null || echo apisix/)
                        if echo "$changed" | grep -qE '^(apisix/|da_services/api/|helm/environments/dev/oan-da-services-gateway\\.yaml$)'; then
                            echo "=== gateway files changed: deploying ==="
                        elif ! helm -n "${DA_NAMESPACE:-da-app}" status da-services-gateway >/dev/null 2>&1; then
                            echo "=== da-services-gateway is not installed: deploying ==="
                        else
                            echo "=== gateway unchanged and installed: skipping ==="
                            exit 0
                        fi
                        bash ci/deploy-gateway.sh
                    '''
                }
            }
        }

        // Through APISIX on oan_dev by its private address: public DNS may still point elsewhere.
        stage('Verify (oan_dev)') {
            when { beforeAgent true; branch 'develop' }
            agent { label 'oan-ci-deploy' }
            steps {
                sh '''
                    host=da-services.development.oanstaging.com
                    url="https://$host/v1/da-services/health"
                    for i in $(seq 1 10); do
                        body=$(curl -sL -m 10 --resolve "$host:443:$OAN_DEV_EDGE_IP" "$url" || true)
                        echo "attempt $i: $url -> $body"
                        echo "$body" | grep -q '"status": *"healthy"' && exit 0
                        sleep 6
                    done
                    echo "da-services did not report healthy at $url"
                    exit 1
                '''
            }
        }

        // ---- staging -> Helm release `da-services` on the QA cluster (qa.oanstaging.com), as oan_a2c ----
        // Runs after the DA node staging deploy above, so a problem here never blocks it. Installs the promoted
        // archive with helm/environments/staging; the kubeconfig is the jenkins-qa-deployer ServiceAccount
        // (oan-helm docs/qa-cluster.md), whose oan-cluster-access lists namespace da-app.
        stage('staging → QA cluster (helm)') {
            when { beforeAgent true; branch 'staging' }
            agent { label 'oan-ci-deploy' }   // VPN agent with helm and kubectl; must reach the QA API
            options { timeout(time: 45, unit: 'MINUTES') }
            steps {
                checkout scm
                unstash 'built-chart'
                withCredentials([file(credentialsId: 'oan-qa-kubeconfig', variable: 'KUBECONFIG')]) {
                    sh 'DEPLOY_ENV=staging bash ci/deploy-helm-dev.sh'
                }
            }
        }

        // staging -> APISIX routes on the QA cluster, every staging build (the first installs them).
        stage('staging → QA cluster (apisix)') {
            when { beforeAgent true; branch 'staging' }
            agent { label 'oan-ci-deploy' }
            options { timeout(time: 15, unit: 'MINUTES') }
            steps {
                checkout scm
                sh 'rm -rf built-gateway-chart'
                unstash 'built-gateway-chart'
                withCredentials([file(credentialsId: 'oan-qa-kubeconfig', variable: 'KUBECONFIG')]) {
                    sh 'GATEWAY_VALUES=helm/environments/staging/oan-da-services-gateway.yaml bash ci/deploy-gateway.sh'
                }
            }
        }

        // ---- Helm chart publish ----
        // Adds the archive built by 'Package chart' to repository/<branch>/ of oan-helm (develop or staging), only
        // after the oan_dev / QA deploy succeeded.
        stage('Publish chart') {
            when { anyOf { branch 'develop'; branch 'staging' } }
            steps {
                // UNSTABLE, not failed: the image is already deployed.
                catchError(buildResult: 'UNSTABLE', stageResult: 'UNSTABLE') {
                    withCredentials([usernamePassword(credentialsId: 'oan-helm-fork-token', usernameVariable: 'GH_USER', passwordVariable: 'GH_TOKEN')]) {
                        sh 'python3 ci/package_chart.py publish'
                    }
                }
            }
        }

        stage('Publish gateway chart') {
            when {
                beforeAgent true
                anyOf {
                    branch 'staging'
                    allOf {
                        branch 'develop'
                        anyOf {
                            changeset 'apisix/**'
                            changeset 'da_services/api/**'
                            changeset 'helm/environments/dev/oan-da-services-gateway.yaml'
                        }
                    }
                }
            }
            steps {
                // UNSTABLE, not failed: the routes are already installed.
                catchError(buildResult: 'UNSTABLE', stageResult: 'UNSTABLE') {
                    withCredentials([usernamePassword(credentialsId: 'oan-helm-fork-token', usernameVariable: 'GH_USER', passwordVariable: 'GH_TOKEN')]) {
                        sh 'python3 ci/package_chart.py gateway-publish'
                    }
                }
            }
        }
    }

    post {
        failure {
            echo "da-services ${env.BRANCH_NAME} build failed (namespace ${env.K8S_NAMESPACE}). Check the stage logs above; 'helm history ${env.HELM_RELEASE} -n ${env.K8S_NAMESPACE}' on the DA node shows the release state."
        }
    }
}
