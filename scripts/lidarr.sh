while true; do
  kubectl port-forward svc/lidarr 8686:80 -n media
done
