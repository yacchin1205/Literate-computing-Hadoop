# Same LC4RI base used by CoursewareHub-LC_deploy PR #1.
FROM niicloudoperation/notebook@sha256:2823b779674971aff907016f0c6f640b21f99a712bb05842fa556d8e53b2ed8a
USER root
RUN python3 -m pip uninstall -y ansible \
 && python3 -m pip install --no-cache-dir ansible==9.13.0 ansible-core==2.16.14 \
      jupyter-server==1.24.0 netaddr==1.3.0 pandas==2.2.3 zk-shell==1.3.4 \
 && apt-get update && apt-get install -y --no-install-recommends tree \
 && mkdir -p /home/jovyan/work /home/jovyan/.ssh \
 && chown -R 1000:100 /home/jovyan/work /home/jovyan/.ssh
USER $NB_USER
