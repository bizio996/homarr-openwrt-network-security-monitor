# Public release checklist

- [ ] Review ownership, replace the placeholder MIT copyright line if desired, and choose a repository name.
- [ ] Validate last uploaded app.py matches the version actually deployed on TrueNAS. The packaged code is a historical snapshot, not live access to your NAS.
- [ ] Confirm `network/health` and all four external traffic windows on the live instance.
- [ ] Import all 8 widgets and verify layouts, intervals, timezone, and empty-state messages in Homarr 2.x.
- [ ] Validate router ACL read-only grants and no elevated ubus privileges.
- [ ] Inspect source, widget JSON, screenshot EXIF and Git history for secrets/IP addresses/MACs.
- [ ] Verify compose file and `.env.example` with sanitized placeholders.
- [ ] Set GitHub repository initially **private** until checks are complete; make public deliberately.
- [ ] Tag `v0.2.8-alpha` after live tests, not `v1.0.0` prematurely.
- [ ] Prepare original screenshots from demo data (not personal dashboards).
- [ ] Publish Homarr Workshop description and link to the final GitHub repository only after validation.
