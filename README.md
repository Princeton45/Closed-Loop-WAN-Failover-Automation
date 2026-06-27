# Closed Loop WAN Failover Automation
 
A self healing dual WAN lab. A Python service running on a Rocky Linux host watches the quality of a primary WAN link, and when that link degrades it automatically drains traffic onto a backup link by reprogramming the router's OSPF cost over RESTCONF. Once the primary link is proven stable again, it moves traffic back. No human touches the CLI.
 
I built this because the manual version of this exact task was part of my day job. When a transport link started flapping in the field, I would log into the routers and retune routing metrics by hand to keep users off the bad path. This project takes that judgment and turns it into a control loop that does it on its own.
 
## What this project does
 
The system runs a continuous loop: measure the link, decide whether it is healthy, and act if it is not.
 
1. The router measures its own primary WAN link using IP SLA (a small ICMP probe it fires every few seconds).
2. A Python service reads those measurements from the router over RESTCONF.
3. A state machine decides whether the link is healthy or degraded, using logic designed so that brief blips do not trigger anything and so the automation itself never oscillates.
4. If the link is degraded, the service raises the OSPF cost on the primary interface over RESTCONF. OSPF reconverges and traffic shifts to the backup path. If the link recovers and stays good, it restores the cost and traffic returns.
The interesting engineering is not the loop itself. It is the safety logic wrapped around it, which I explain in the decision logic section below.
 
## Why the design is shaped this way
 
The whole point of a failover tool is stability, so the worst possible failure mode is a tool that causes the very flapping it is supposed to prevent. Everything in the decision logic exists to prevent that. Brief degradations are ignored. A recovery has to last a while before the tool trusts it. Config changes are written in a way that is safe to repeat. That discipline is what separates this from a simple "if bad then change config" script.
 
## Lab topology
 
The lab is two sites joined by two WAN paths, built entirely inside GNS3 with a Rocky Linux virtual machine bridged in to run the automation.
 
![GNS3](https://github.com/Princeton45/Closed-Loop-WAN-Failover-Automation/blob/main/images/gns3.png)

![vmware](https://github.com/Princeton45/Closed-Loop-WAN-Failover-Automation/blob/main/images/vmware.png)
 
*Screenshot to add here: the full GNS3 canvas (the topology image). This is the anchor image of the whole writeup, so it goes right at the top of this section.*
 
Two sites. HQ on the left, Remote on the right. Each site has a WAN edge router (R1, R2) and a layer 3 switch (HQ-CORE, RMT-CORE) that handles routing between the user and server VLANs over switched virtual interfaces. End hosts live in real VLANs. The two routers are joined by a primary and a backup link. Management is out of band: a separate switch (SW-MGT) connects both routers and the Rocky VM through a GNS3 Cloud node bound to a VMware vmnet, so the automation reaches the routers without touching the data plane.
 
The logical version of the same topology:
 
```mermaid
flowchart TB
    subgraph MGMT["OOB management  10.99.99.0/24"]
        VM["Rocky Linux VM<br/>controller host  .50"]
        CLOUD["GNS3 Cloud<br/>vmnet2"]
        SWM["SW-MGT"]
        VM --- CLOUD
        CLOUD --- SWM
    end
 
    subgraph HQ["HQ site"]
        R1["R1<br/>WAN edge / RESTCONF"]
        HQC["HQ-CORE<br/>L3 switch"]
        PC1["HQ-PC1<br/>vlan 10"]
        SR1["HQ-SRV1<br/>vlan 20"]
        R1 ---|10.1.0.0/30| HQC
        HQC --- PC1
        HQC --- SR1
    end
 
    subgraph RMT["Remote site"]
        R2["R2<br/>WAN edge / RESTCONF"]
        RMC["RMT-CORE<br/>L3 switch"]
        PC2["RMT-PC1<br/>vlan 30"]
        SR2["RMT-SRV1<br/>vlan 40"]
        R2 ---|10.2.0.0/30| RMC
        RMC --- PC2
        RMC --- SR2
    end
 
    SWM -.- R1
    SWM -.- R2
    R1 ---|primary 10.0.12.0/30| R2
    R1 ---|backup 10.0.21.0/30| R2
```
 
### Addressing plan
 
| Network | Purpose | Devices |
|---|---|---|
| 10.99.99.0/24 | Out of band management | R1 Gi3 .11, R2 Gi3 .12, Rocky VM .50 |
| 10.0.12.0/30 | Primary WAN (the monitored link) | R1 Gi1 .1, R2 Gi1 .2 |
| 10.0.21.0/30 | Backup WAN | R1 Gi2 .1, R2 Gi2 .2 |
| 10.1.0.0/30 | R1 to HQ-CORE uplink | R1 Gi4 .1, HQ-CORE Eth0/1 .2 |
| 10.2.0.0/30 | R2 to RMT-CORE uplink | R2 Gi4 .1, RMT-CORE Eth1/1 .2 |
| 192.168.10.0/24 | HQ users, vlan 10 | gateway HQ-CORE .1, HQ-PC1 .100 |
| 192.168.20.0/24 | HQ servers, vlan 20 | gateway HQ-CORE .1, HQ-SRV1 .100 |
| 192.168.30.0/24 | Remote users, vlan 30 | gateway RMT-CORE .1, RMT-PC1 .100 |
| 192.168.40.0/24 | Remote servers, vlan 40 | gateway RMT-CORE .1, RMT-SRV1 .100 |
 
OSPF area 0 runs on both routers and both core switches. The host facing switched virtual interfaces (vlan 10, 20, 30, 40) are set passive in OSPF, so the subnets are advertised but the switches send no hellos toward the access ports. The routers have nothing passive because every routing interface on them peers with another router. Normal primary cost is 10 and backup cost is 50, so OSPF prefers the primary by default. The automation raises the primary cost to 1000 to force a failover and sets it back to 10 to recover.
 
## How the pieces work together
 
The automation is five small Python files. Four do one job each and one ties them together. Keeping them separate is deliberate: the decision logic touches nothing on the network, which means I can test all the tricky failover behavior on my laptop with the lab turned off.
 
```mermaid
flowchart LR
    subgraph CTRL["controller.py  (systemd service, 5s loop)"]
        direction TB
        C["collector.py<br/>read IP SLA via RESTCONF GET"]
        D["decide.py<br/>state machine and hysteresis"]
        A["actuator.py<br/>set OSPF cost via RESTCONF PATCH"]
        C --> D --> A
    end
    R1["R1 (IOS XE)<br/>IP SLA 1 probing 10.0.12.2"]
    OUT["OSPF reconverges<br/>traffic on primary or backup"]
 
    R1 -->|loss and latest return code| C
    A -->|cost 10 normal, cost 1000 drain| R1
    R1 --> OUT
```
 
`probe.py` is the throwaway first script. Its only job was to prove I could authenticate to the router over RESTCONF and pull back the raw IP SLA JSON so I could read its structure. It is not part of the running system, but it is in the repo because it is how I discovered the actual field names my IOS XE version uses. Reading the live JSON instead of trusting a blog is the habit that saved me later.
 
`collector.py` reads the IP SLA operational data over RESTCONF and reduces it to the few numbers the rest of the system needs: a loss percentage, the round trip time, and the latest probe return code. It hides all of the YANG and JSON details so nothing downstream has to care about them. It is also where the most important bug fix lives (see the loss calculation story below).
 
`decide.py` is the brain and it is pure logic. It imports nothing that talks to the network. You feed it one poll's worth of metrics and it returns one of three decisions: do nothing, fail over, or revert. It holds the current state and the streak counters between calls. Because it is pure, the file ships with its own unit tests that prove the failover and anti flap behavior without any lab running at all.
 
`actuator.py` writes the OSPF cost back to the router with a RESTCONF PATCH. It returns true or false instead of throwing, so the controller can decide what to do when a write fails. Writing one leaf with PATCH means re applying the same value is harmless, which matters in a loop.
 
`controller.py` is the service. It runs the loop every five seconds, calls the collector, hands the metrics to the decision logic, and when a decision comes back it calls the actuator with retries. It logs every cycle so the whole thing leaves an audit trail. It runs under systemd and logs to the journal.
 
## The decision logic
 
This is the part worth understanding deeply, because it is the part an interviewer will dig into and it is the part that makes the project more than a tutorial.
 
There are two states, HEALTHY and FAILED_OVER, and the transitions between them are intentionally hard to trigger.
 
```mermaid
stateDiagram-v2
    [*] --> HEALTHY
    HEALTHY --> FAILED_OVER: 3 bad polls in a row / raise primary cost to 1000
    FAILED_OVER --> HEALTHY: 6 good polls in a row and 60s dwell elapsed / restore cost to 10
```
 
A single poll is judged bad if loss crosses a threshold, or round trip time crosses a threshold, or the router cannot be read at all (an unreadable router is treated as a degraded link, not a crash).
 
The reason this does not flap comes down to three ideas working together.
 
The first is hysteresis. It takes three bad polls in a row to fail over, but six good polls in a row to even consider reverting. The bar to leave a state is higher than the bar to enter it, so the system settles instead of bouncing.
 
The second is a dwell timer. Even after the primary looks healthy again, the system refuses to revert until at least sixty seconds have passed since the failover. This stops a link that recovers and immediately degrades again from yanking traffic back and forth.
 
The third is graceful draining instead of a hard cut. The tool raises the OSPF cost rather than shutting the interface down. OSPF then makes the routing decision the way it is designed to. The path is never torn away, it just becomes unattractive, so traffic drains cleanly to the backup.
 
If someone asks me what stops my anti flap tool from itself flapping, the answer is those three things. That sentence is the whole reason the design is shaped the way it is.
 
## Running the demo
 
The demo that proves it works is a host to host traceroute that visibly changes path while the link degrades, with connectivity never dropping.
 
First confirm the plumbing is healthy. RESTCONF returns the router hostname over TLS:
 
![RESTCONF reachable](screenshots/restconf-curl-success.png)
 
*Screenshot to add: the curl command returning the hostname JSON from R1. This single image proves TLS, the certificate, and authentication all work, which was most of the battle.*
 
The router's IP SLA is up and logging successes on a clean link:
 
![IP SLA statistics](screenshots/ip-sla-stats.png)
 
*Screenshot to add: `show ip sla statistics 1` on R1 showing a return code of OK and a growing success count.*
 
The decision logic passes its own tests with the lab off:
 
![State machine tests pass](screenshots/state-machine-tests.png)
 
*Screenshot to add: the terminal output of running decide.py and seeing the unit tests pass. This is the image that says I tested my logic, not just my luck.*
 
Now the failover itself. Start the service, then open a continuous traceroute from a host at HQ to a host at the remote site and watch the middle hop.
 
On a clean link, traffic crosses the primary WAN:
 
![Traceroute over primary](screenshots/traceroute-primary.png)
 
*Screenshot to add: traceroute from HQ-PC1 to RMT-PC1, middle hop going through 10.0.12.2 (the primary).*
 
Inject loss on the primary link in GNS3 and the service fails over. The same traceroute now crosses the backup WAN, and the host never lost reach:
 
![Traceroute over backup](screenshots/traceroute-backup.png)
 
*Screenshot to add: the same traceroute after failover, middle hop now going through 10.0.21.2 (the backup).*
 
The proof is the service log. It shows the state, the live metrics, and the moment it decided to fail over and later to revert, all with timestamps:
 
![Controller failover log](screenshots/controller-failover-log.png)
 
*Screenshot to add: journalctl output of the controller showing the state going HEALTHY, then a FAIL_OVER decision, then back to HEALTHY on REVERT. This timestamped trail is the strongest single piece of evidence in the whole project.*
 
## Problems I hit and how I solved them
 
I am keeping this section detailed on purpose. The clean parts of this project are short. The real work was here, and these are the stories I can actually defend in a conversation.
 
### TLS would not even handshake
 
The first curl to the router failed with a TLS handshake failure before authentication ever ran. That told me the network path was fine and the two sides simply could not agree on terms. The cause was that my Rocky Linux 10 host enforces a modern crypto policy that rejects old signature algorithms, and the router was presenting a temporary certificate it had generated on its own, signed with SHA1. The `-k` flag did not help because that only skips trust checks, not the rejection of a weak signature algorithm. The real fix was to give the router a proper SHA256 certificate with a 2048 bit key, rather than weakening the host. Fixing the device instead of dumbing down the client is the production correct move and I can say so.
 
### The certificate refused to generate
 
Generating that certificate then failed twice for two different reasons. First the router kept reusing a 1024 bit key even after I asked for 2048, so I had to zeroize the key, regenerate it, and verify the size by actually reading the modulus rather than trusting the command output. Second, even with a good key, enrollment failed with the message "validity period start later than end." Turning on the PKI debug showed the router was stamping the certificate's start date after its end date. The old router image had a hardcoded maximum certificate date that fell before the lab's 2026 clock, so the validity window came out inverted. The fix was counterintuitive: roll the router's clock back to an earlier year, generate the certificate there, then roll the clock forward again. The certificate dates do not matter to my clients because they skip expiry checks in the lab, so all I needed was for generation to succeed.
 
### RESTCONF answered, then refused me
 
With TLS fixed, curl returned a 404. That meant RESTCONF itself was not running, so the web server treated the path as unknown. Enabling RESTCONF moved me to a 401. The 401 was AAA: the router had no method wired up to validate the login against its local user database, and on top of that I had never actually created the API user on that router. Adding the local user and binding the HTTPS server to local AAA authentication fixed it. The lesson I took is that 404 versus 401 versus a structured RESTCONF error each point at a different layer, and reading which one you got saves a lot of guessing.
 
### IP SLA reported nothing but failures
 
The probe data came back as all failures even with no impairment. The router was timing out its own ICMP probe, which is a reachability problem, not a tuning problem. I also learned that the SLA threshold value does not cause failures at all, it only flags slow probes, so changing it was never going to help. Confirming the path with a sourced ping is how I separated a real link problem from an SLA config problem.
 
### The loss number was useless
 
Once data flowed, my loss percentage sat at zero even while failures climbed. The bug was that I was computing loss from the router's lifetime success and failure counters. A dozen new failures against hundreds of past successes rounds to nothing, so the signal was hopelessly slow. The fix was to compute loss as the change between polls, meaning failures in this interval over probes in this interval, and to also read the latest return code as an instant health bit. That made the metric react immediately to a degrading link, which the state machine needs.
 
### The config write got rejected
 
Writing the OSPF cost back failed with a 400. The path in my code did not match how my router actually nests the cost leaf. I had assumed an extra layer in the model that does not exist on my image. The discovery method that fixed it was simple: GET the interface, read the real JSON structure, and match my URL and body to it exactly. The rule underneath the 400 was that the top level key of the JSON body has to name the last node in the URL. My URL ended at the cost leaf while my body was shaped for the container above it, so the two did not agree. Once they matched, the write went through and traffic moved.
 
## Tech stack
 
Python with the requests library for the automation. RESTCONF over HTTPS with YANG modeled data for talking to the routers. Cisco IOS XE routers and IOSvL2 layer 3 switches in GNS3, with VPCS for end hosts. OSPF for routing and IP SLA for link measurement. A Rocky Linux 10 virtual machine in VMware Workstation, bridged into GNS3 through a Cloud node and a vmnet, running the service under systemd and logging to journald. The whole lab and host run on a single Linux Mint laptop.
 
## What I would build next
 
I scoped this to a single monitored link with a hardcoded configuration so I could get the core loop solid. The honest next steps, in the order I would do them:
 
Make it genuinely event driven. Right now the service polls every five seconds, which is telemetry driven, not event driven. The router can be told to push a syslog message the instant a threshold is crossed using an Embedded Event Manager applet, and the service can react to that instead of polling. That is the upgrade that earns the term.
 
Stream the telemetry. Replace RESTCONF polling with a gNMI subscription so the router streams counters continuously over gRPC, which is the modern and higher scale approach.
 
Make it config as data. Move the router address, thresholds, costs, and interface into a YAML file and add a second router, so one service manages many links from a data file instead of hardcoded values.
 
Harden it for production. The lab takes shortcuts I would never take on a real network: a self signed certificate with expiry checks skipped, a local user instead of central AAA, and a single device. The production version validates against a real certificate authority, authenticates through TACACS+, and runs against many routers.
 
## What I took away from it
 
The code is the small part. Most of the time went into the layers underneath: TLS and certificates, the device's AAA, the YANG data model, and the link itself. Every one of those failed in a different way and each failure pointed at a specific layer once I learned to read it. The part of the project I am most confident explaining is not the happy path, it is the troubleshooting, because I lived through all of it. If I come back to this in ten years, the thing to remember is that the architecture is just sense, decide, act, and the entire craft is in making the decide step refuse to overreact.
